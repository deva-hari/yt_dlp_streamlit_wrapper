import streamlit as st
import json
import subprocess
import os
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

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


def build_yt_dlp_cmd(video_id, output_dir, title=None):
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
        if config["quality"] == "best":
            pass  # Don't add -f best, let yt-dlp pick and merge best
        else:
            cmd += ["-f", config["quality"]]
        if config["thumbnails"] == "embed":
            cmd += ["--embed-thumbnail"]
        elif config["thumbnails"] == "write":
            cmd += ["--write-thumbnail"]

    if config["metadata"] == "embed":
        cmd += ["--embed-metadata"]
    elif config["metadata"] == "write":
        cmd += ["--write-info-json"]

    if config["subtitles"] != "none":
        cmd += [
            "--write-subs",
            "--sub-lang",
            config["subtitles"],
            "--sub-format",
            "best",
            "--embed-subs",
        ]

    if config["sponsorblock"] == "mark":
        cmd += ["--sponsorblock-mark", "all"]
    elif config["sponsorblock"] == "remove":
        cmd += ["--sponsorblock-remove", "all"]

    # If video_id looks like a full URL, use as-is; else, prepend base URL
    if isinstance(video_id, str) and video_id.startswith("http"):
        cmd += [video_id]
    else:
        cmd += ["https://www.youtube.com/watch?v=" + video_id]
    return cmd


def download_video(video, output_path):
    video_id = video["id"]
    title = video["title"]
    try:
        cmd = build_yt_dlp_cmd(video_id, output_path, title=title)
        subprocess.run(cmd, check=True)
        return {
            "title": title,
            "status": "Downloaded",
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
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


# === Streamlit UI ===
st.set_page_config(page_title="YT Playlist Downloader Advanced", layout="wide")
st.title("📺 Advanced YouTube Playlist Downloader")

# Sidebar Config Panel
st.sidebar.header("⚙️ Configuration")
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
config["subtitles"] = st.sidebar.selectbox(
    "Subtitles",
    ["all", "en", "none"],
    index=["all", "en", "none"].index(config["subtitles"]),
)
config["sponsorblock"] = st.sidebar.selectbox(
    "SponsorBlock",
    ["mark", "remove", "skip"],
    index=["mark", "remove", "skip"].index(config["sponsorblock"]),
)
config["max_concurrent"] = st.sidebar.slider(
    "Max Parallel Downloads", 1, 5, value=config["max_concurrent"]
)
config["output_path"] = st.sidebar.text_input(
    "📁 Output Path", value=config.get("output_path", str(Path.cwd()))
)

if st.sidebar.button("💾 Save Settings"):
    save_config(config)
    st.sidebar.success("Config saved!")

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
                    output_dir = Path(config["output_path"]) / sanitize_filename(
                        data["title"]
                    )
                    output_dir.mkdir(parents=True, exist_ok=True)
                    results = []
                    with ThreadPoolExecutor(
                        max_workers=config["max_concurrent"]
                    ) as executor:
                        futures = {
                            executor.submit(download_video, vid, output_dir): vid
                            for vid in selected_videos
                        }
                        for future in as_completed(futures):
                            result = future.result()
                            st.write(f"{result['title']}: {result['status']}")
                            results.append(result)
                    log_history(results)
                    st.success(
                        f"✅ Downloaded {sum(1 for r in results if r['status'] == 'Downloaded')} / {len(results)} videos."
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
                output_dir = Path(config["output_path"])
                output_dir.mkdir(parents=True, exist_ok=True)
                results = []
                with ThreadPoolExecutor(
                    max_workers=config["max_concurrent"]
                ) as executor:
                    # Fetch real titles for each video
                    video_objs = []
                    for url in urls:
                        title = fetch_video_title(url)
                        video_objs.append({"id": url, "title": title})
                    futures = {
                        executor.submit(download_video, vid, output_dir): vid
                        for vid in video_objs
                    }
                    for future in as_completed(futures):
                        result = future.result()
                        st.write(f"{result['title']}: {result['status']}")
                        results.append(result)
                log_history(results)
                st.success(
                    f"✅ Downloaded {sum(1 for r in results if r['status'] == 'Downloaded')} / {len(results)} videos."
                )
