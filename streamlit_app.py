import streamlit as st

# Set Streamlit page config as the very first Streamlit command
st.set_page_config(page_title="YT Downloader Advanced", layout="wide")
import json
import subprocess
import os
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import tempfile
import zipfile

CONFIG_FILE = "config.json"
COOKIE_FILE = "cookies.txt"
HISTORY_FILE = "download_history.json"

DEFAULT_CONFIG = {
    "quality": "best",
    "thumbnails": "embed",
    "metadata": "embed",
    "subtitles": "all",
    "sponsorblock": "mark",
    "audio_only": False,
    "max_concurrent": 2,
    "output_path": str(Path.cwd()),
}


def load_config():
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            user_cfg = json.load(f)
            config.update(user_cfg)  # Merge user config over defaults
    return config


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


config = load_config()


def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name)


def get_playlist_info(url):
    try:
        result = subprocess.run(
            [
                "yt-dlp",
                "--flat-playlist",
                "--dump-single-json",
                "--no-warnings",
                "--skip-download",
                "--cookies",
                COOKIE_FILE,
                url,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        return {
            "title": sanitize_filename(data.get("title", "Untitled Playlist")),
            "videos": [
                {
                    "id": entry["id"],
                    "title": sanitize_filename(entry.get("title", f"Video {i+1}")),
                }
                for i, entry in enumerate(data.get("entries", []))
            ],
        }
    except Exception as e:
        st.error(f"Error getting playlist info: {e}")
        return None


def get_unique_filename(output_dir, base_name, ext):
    """Return a unique filename in output_dir, appending _1, _2, etc. if needed."""
    candidate = f"{base_name}.{ext}"
    i = 1
    while (output_dir / candidate).exists():
        candidate = f"{base_name}_{i}.{ext}"
        i += 1
    return candidate


def get_video_formats(url):
    """Fetch available formats for a video using yt-dlp -F."""
    try:
        result = subprocess.run(
            ["yt-dlp", "-F", url], capture_output=True, text=True, check=True
        )
        lines = result.stdout.splitlines()
        formats = []
        for line in lines:
            if re.match(r"^\d+", line.strip()):
                parts = line.split()
                if len(parts) > 1:
                    formats.append({"id": parts[0], "desc": " ".join(parts[1:])})
        return formats
    except Exception:
        return []


def build_yt_dlp_cmd(video_id, output_dir, title=None, format_id=None):
    # If title is provided, use it for output filename, else use yt-dlp default
    if title:
        base_name = sanitize_filename(title)
        ext = "mp3" if config["audio_only"] else "%(ext)s"
        unique_name = get_unique_filename(output_dir, base_name, ext)
        out_template = (
            unique_name if "%(" not in unique_name else f"{base_name}.%(ext)s"
        )
    else:
        out_template = "%(title)s.%(ext)s"
    cmd = [
        "yt-dlp",
        "--no-cache-dir",
        "-o",
        str(output_dir / out_template),
        "--cookies",
        COOKIE_FILE,
    ]

    if config["audio_only"]:
        cmd += ["-f", "bestaudio", "--extract-audio", "--audio-format", "mp3"]
        # Always embed thumbnail for audio if not skipped
        if config["thumbnails"] != "skip":
            cmd += ["--embed-thumbnail"]
    else:
        # Prefer mp4 for video
        if format_id:
            cmd += ["-f", format_id]
        elif config["quality"] == "best":
            cmd += ["-f", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"]
        else:
            # If user selected a specific quality, still prefer mp4
            cmd += ["-f", f"bestvideo[height={config['quality'].replace('p','')}][ext=mp4]+bestaudio[ext=m4a]/best[height={config['quality'].replace('p','')}][ext=mp4]/best[height={config['quality'].replace('p','')}]" if 'p' in config['quality'] else config['quality']]
        if config["thumbnails"] == "embed":
            cmd += ["--embed-thumbnail"]
        elif config["thumbnails"] == "write":
            cmd += ["--write-thumbnail"]

    if config["metadata"] == "embed":
        cmd += ["--embed-metadata"]
    elif config["metadata"] == "write":
        cmd += ["--write-info-json"]

    # Multi-language subtitle support
    if config["subtitles"] and "none" not in config["subtitles"]:
        sub_langs = ",".join(config["subtitles"])
        cmd += [
            "--write-subs",
            "--sub-lang",
            sub_langs,
            "--sub-format",
            "best",
            "--embed-subs",
        ]

    if config["sponsorblock"] == "mark":
        cmd += ["--sponsorblock-mark", "all"]
    elif config["sponsorblock"] == "remove":
        cmd += ["--sponsorblock-remove", "all"]

    # Proxy support
    if config.get("proxy"):
        cmd += ["--proxy", config["proxy"]]

    # If video_id looks like a full URL, use as-is; else, prepend base URL
    if isinstance(video_id, str) and video_id.startswith("http"):
        cmd += [video_id]
    else:
        cmd += ["https://www.youtube.com/watch?v=" + video_id]
    return cmd


def download_video(
    video, output_path, format_id=None, max_retries=2, progress_callback=None
):
    video_id = video["id"]
    title = video["title"]
    attempt = 0
    while attempt < max_retries:
        try:
            cmd = build_yt_dlp_cmd(
                video_id, output_path, title=title, format_id=format_id
            )
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            progress = 0
            for line in process.stdout:
                print(line, end="")  # Print logs to terminal
                if progress_callback:
                    # Try to parse yt-dlp progress (very basic, can be improved)
                    if "%" in line:
                        try:
                            percent = float(re.search(r"(\d{1,3}\.\d)%", line).group(1))
                            progress_callback(percent / 100)
                        except Exception:
                            pass
                # Optionally, collect output for error reporting
            process.wait()
            if process.returncode == 0:
                if progress_callback:
                    progress_callback(1.0)
                return {
                    "title": title,
                    "status": "Downloaded",
                    "timestamp": datetime.now().isoformat(),
                }
            else:
                attempt += 1
                if attempt >= max_retries:
                    return {
                        "title": title,
                        "status": f"Failed after {max_retries} attempts.",
                        "timestamp": datetime.now().isoformat(),
                    }
        except Exception as e:
            attempt += 1
            if attempt >= max_retries:
                return {
                    "title": title,
                    "status": f"Failed: {str(e)[:100]}",
                    "timestamp": datetime.now().isoformat(),
                }


def log_history(entries):
    history = []
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r") as f:
            history = json.load(f)
    history.extend(entries)
    with open(HISTORY_FILE, "w") as f:
        json.dump(history, f, indent=2)


def fetch_video_title(url):
    """Fetch the video title using yt-dlp --dump-json."""
    try:
        result = subprocess.run(
            ["yt-dlp", "--dump-json", "--no-warnings", "--skip-download", url],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(result.stdout)
        return sanitize_filename(data.get("title", url))
    except Exception:
        return sanitize_filename(url)


# --- Temp file/session management for browser downloads ---
if "temp_files" not in st.session_state:
    st.session_state["temp_files"] = []
if "temp_dir" not in st.session_state:
    st.session_state["temp_dir"] = tempfile.TemporaryDirectory()


def cleanup_temp_files():
    for fp in st.session_state["temp_files"]:
        try:
            if os.path.exists(fp):
                os.remove(fp)
        except Exception:
            pass
    st.session_state["temp_files"] = []
    # Clean up temp dir
    if "temp_dir" in st.session_state:
        try:
            st.session_state["temp_dir"].cleanup()
        except Exception:
            pass
        st.session_state["temp_dir"] = tempfile.TemporaryDirectory()


# Clean up temp files on page refresh
if st.sidebar.button("🧹 Clean Up Temp Files"):
    cleanup_temp_files()
    st.sidebar.success("Temporary files cleaned up!")

# === Streamlit UI ===
st.title("📺 Advanced YouTube Downloader")

# Sidebar Config Panel
st.sidebar.header("⚙️ Configuration")
output_mode = st.sidebar.radio(
    "Output Mode",
    ["Save to Folder", "Download via Browser"],
    index=0,
    help="Choose where to save downloaded files.",
)
config["output_mode"] = output_mode
if output_mode == "Save to Folder":
    config["output_path"] = st.sidebar.text_input(
        "📁 Output Path", value=config.get("output_path", str(Path.cwd()))
    )
else:
    st.sidebar.info(
        "Files will be available for download in the browser after completion."
    )

config["quality"] = st.sidebar.selectbox(
    "Video Quality",
    ["best", "720p", "bestvideo+bestaudio"],
    index=["best", "720p", "bestvideo+bestaudio"].index(config["quality"]),
)
config["audio_only"] = st.sidebar.checkbox("Audio Only", value=config["audio_only"])
config["thumbnails"] = st.sidebar.selectbox(
    "Thumbnails",
    ["embed", "write", "skip"],
    index=["embed", "write", "skip"].index(config["thumbnails"]),
)
config["metadata"] = st.sidebar.selectbox(
    "Metadata",
    ["embed", "write", "none"],
    index=["embed", "write", "none"].index(config["metadata"]),
)
# Multi-language subtitle selection
subtitle_options = ["all", "en", "es", "fr", "de", "ru", "hi", "ja", "ko", "zh", "none"]
config["subtitles"] = st.sidebar.multiselect(
    "Subtitles (multi-select)",
    subtitle_options,
    default=[config["subtitles"]] if config["subtitles"] in subtitle_options else [],
)
config["sponsorblock"] = st.sidebar.selectbox(
    "SponsorBlock",
    ["mark", "remove", "skip"],
    index=["mark", "remove", "skip"].index(config["sponsorblock"]),
)
config["max_concurrent"] = st.sidebar.slider(
    "Max Parallel Downloads", 1, 5, value=config["max_concurrent"]
)
# Proxy support
config["proxy"] = st.sidebar.text_input(
    "Proxy (optional, e.g. socks5://127.0.0.1:1080)", value=config.get("proxy", "")
)

# yt-dlp update button
if st.sidebar.button("⬆️ Update yt-dlp"):
    with st.spinner("Updating yt-dlp..."):
        result = subprocess.run(
            ["python", "-m", "pip", "install", "-U", "yt-dlp"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            st.sidebar.success("yt-dlp updated successfully!")
        else:
            st.sidebar.error(f"Update failed: {result.stderr}")

if st.sidebar.button("💾 Save Settings"):
    save_config(config)
    st.sidebar.success("Config saved!")

# --- Download History UI ---
st.sidebar.markdown("---")
st.sidebar.markdown("### 📜 Download History")
history = []
if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)
if history:
    import pandas as pd

    df = pd.DataFrame(history)
    st.sidebar.dataframe(
        df.tail(20)[["title", "status", "timestamp"]], use_container_width=True
    )
else:
    st.sidebar.info("No download history yet.")

# --- Mode Selector ---
mode = st.radio("Select Download Mode", ["Playlist", "Single Video"], horizontal=True)

# Input URLs
if mode == "Playlist":
    st.subheader("🔗 Playlist URLs")
    user_input = st.text_area("Paste YouTube playlist URLs (one per line)", height=200)
    if st.button("🧠 Process Playlists"):
        urls = list(set(filter(None, map(str.strip, user_input.splitlines()))))
        playlists = {}
        for url in urls:
            with st.status(f"Processing: {url}", expanded=True):
                info = get_playlist_info(url)
                if info:
                    playlists[url] = info
                    st.success(
                        f"Found {len(info['videos'])} videos in '{info['title']}'"
                    )
        if playlists:
            with open("playlist_data.json", "w") as f:
                json.dump(playlists, f, indent=2)

    # Episode Selector + Downloader
    if os.path.exists("playlist_data.json"):
        with open("playlist_data.json", "r") as f:
            playlists = json.load(f)
        for url, data in playlists.items():
            st.subheader(f"🎞️ {data['title']}")
            all_option = {"id": "__all__", "title": "All"}
            video_options = [all_option] + data["videos"]
            selected_videos = st.multiselect(
                f"Select episodes from: {data['title']}",
                options=video_options,
                format_func=lambda x: x["title"],
                key=url,
            )
            # If "All" is selected, select all videos
            if any(v["id"] == "__all__" for v in selected_videos):
                selected_videos = data["videos"]
            if st.button(f"⬇️ Download Selected from {data['title']}", key=f"btn_{url}"):
                with st.status(
                    "Downloading selected videos...", expanded=True
                ) as status:
                    if config["output_mode"] == "Save to Folder":
                        output_dir = Path(config["output_path"]) / sanitize_filename(
                            data["title"]
                        )
                        output_dir.mkdir(parents=True, exist_ok=True)
                    else:
                        output_dir = Path(st.session_state["temp_dir"].name)
                    results = []
                    filepaths = []
                    for vid in selected_videos:
                        progress_bar = st.progress(0.0, text=f"{vid['title']}")

                        def update_progress(p, bar=progress_bar):
                            bar.progress(p, text=f"{vid['title']}")

                        result = download_video(
                            vid, output_dir, progress_callback=update_progress
                        )
                        st.write(f"{result['title']}: {result['status']}")
                        results.append(result)
                        if (
                            config["output_mode"] == "Download via Browser"
                            and result["status"] == "Downloaded"
                        ):
                            ext = "mp3" if config["audio_only"] else "mp4"
                            candidate = output_dir / f"{sanitize_filename(vid['title'])}.{ext}"
                            if candidate.exists():
                                filepaths.append(candidate)
                            else:
                                # fallback: add .webm if mp4 not found
                                webm_candidate = output_dir / f"{sanitize_filename(vid['title'])}.webm"
                                if webm_candidate.exists():
                                    filepaths.append(webm_candidate)
                    log_history(results)
                    st.success(
                        f"✅ Downloaded {sum(1 for r in results if r['status'] == 'Downloaded')} / {len(results)} videos."
                    )
                    # Show download buttons for each file (browser mode)
                    if config["output_mode"] == "Download via Browser" and filepaths:
                        for fp in filepaths:
                            st.session_state["temp_files"].append(str(fp))
                            with open(fp, "rb") as f:
                                st.download_button(
                                    f"Download {fp.name}",
                                    f,
                                    file_name=fp.name,
                                    key=f"dlbtn_{fp.name}_{url}",
                                )
                        # Show zip download button if browser mode
                        zip_path = (
                            output_dir / f"{sanitize_filename(data['title'])}.zip"
                        )
                        with zipfile.ZipFile(zip_path, "w") as zipf:
                            for fp in filepaths:
                                zipf.write(fp, arcname=fp.name)
                        st.session_state["temp_files"].append(str(zip_path))
                        with open(zip_path, "rb") as fzip:
                            st.download_button(
                                f"Download All as ZIP",
                                fzip,
                                file_name=zip_path.name,
                                key=f"zipbtn_{url}",
                            )
else:
    st.subheader("🔗 Single Video URLs")
    user_input = st.text_area(
        "Paste YouTube video URLs (one per line)", height=200, key="single_video"
    )
    if st.button("⬇️ Download Videos", key="download_single_videos"):
        urls = list(set(filter(None, map(str.strip, user_input.splitlines()))))
        if not urls:
            st.warning("No video URLs provided.")
        else:
            with st.status("Downloading videos...", expanded=True) as status:
                if config["output_mode"] == "Save to Folder":
                    output_dir = Path(config["output_path"])
                    output_dir.mkdir(parents=True, exist_ok=True)
                else:
                    output_dir = Path(st.session_state["temp_dir"].name)
                results = []
                filepaths = []
                for url in urls:
                    title = fetch_video_title(url)
                    progress_bar = st.progress(0.0, text=f"{title}")

                    def update_progress(p, bar=progress_bar):
                        bar.progress(p, text=f"{title}")

                    result = download_video(
                        {"id": url, "title": title},
                        output_dir,
                        progress_callback=update_progress,
                    )
                    st.write(f"{result['title']}: {result['status']}")
                    results.append(result)
                    if (
                        config["output_mode"] == "Download via Browser"
                        and result["status"] == "Downloaded"
                    ):
                        ext = "mp3" if config["audio_only"] else "mp4"
                        candidate = output_dir / f"{sanitize_filename(title)}.{ext}"
                        if candidate.exists():
                            filepaths.append(candidate)
                        else:
                            # fallback: add .webm if mp4 not found
                            webm_candidate = output_dir / f"{sanitize_filename(title)}.webm"
                            if webm_candidate.exists():
                                filepaths.append(webm_candidate)
                log_history(results)
                st.success(
                    f"✅ Downloaded {sum(1 for r in results if r['status'] == 'Downloaded')} / {len(results)} videos."
                )
                # Show download buttons for each file (browser mode)
                if config["output_mode"] == "Download via Browser" and filepaths:
                    for fp in filepaths:
                        st.session_state["temp_files"].append(str(fp))
                        with open(fp, "rb") as f:
                            st.download_button(
                                f"Download {fp.name}",
                                f,
                                file_name=fp.name,
                                key=f"dlbtn_{fp.name}",
                            )

# --- Always show download buttons for temp files at the end ---
if config["output_mode"] == "Download via Browser" and st.session_state["temp_files"]:
    st.markdown("---")
    st.subheader("⬇️ Download Your Files")
    for fp in st.session_state["temp_files"]:
        if os.path.exists(fp):
            with open(fp, "rb") as f:
                st.download_button(
                    f"Download {Path(fp).name}",
                    f,
                    file_name=Path(fp).name,
                    key=f"dlbtn_{Path(fp).name}",
                )
