import streamlit as st
import os
import json
import subprocess
import re
from pathlib import Path

CONFIG_FILE = "config.json"

def load_config():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_config(config):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

# Load config at start
config = load_config()

st.set_page_config(page_title="YouTube Playlist Downloader", layout="wide")

def sanitize_filename(name):
    return re.sub(r'[\\/*?:"<>|]', "", name)

COOKIES_FILE = "cookies.txt"  # Optional: export from browser via extension

def get_playlist_info(url):
    try:
        result = subprocess.run([
            "yt-dlp", "--flat-playlist", "--dump-single-json", "--no-warnings", "--skip-download", "--cookies", COOKIES_FILE, url
        ], capture_output=True, text=True, check=True)
        data = json.loads(result.stdout)
        return {
            "title": sanitize_filename(data.get("title", "Untitled Playlist")),
            "videos": [
                {"id": entry["id"], "title": sanitize_filename(entry.get("title", f"Video {i+1}"))}
                for i, entry in enumerate(data.get("entries", []))
            ]
        }
    except subprocess.CalledProcessError as e:
        st.error(f"Error processing URL: {url}\n{e.stderr}")
        return None

def download_video(video_id, output_dir, config, retries=3):
    for attempt in range(1, retries + 1):
        try:
            cmd = [
                "yt-dlp",
                "--no-cache-dir",
                "-o", str(output_dir / "%(title)s.%(ext)s"),
                f"https://www.youtube.com/watch?v={video_id}",
                "--cookies", COOKIES_FILE
            ]
            # Quality/format
            if config.get("quality"):
                cmd += ["-f", config["quality"]]
            # Thumbnail
            if config.get("thumbnail"):
                cmd += ["--write-thumbnail", "--embed-thumbnail"]
            # Metadata
            if config.get("metadata"):
                cmd += ["--write-info-json"]
            # Subtitles
            if config.get("subtitles") and len(config["subtitles"]) > 0:
                cmd += ["--write-subs", "--embed-subs", "--sub-lang", ",".join(config["subtitles"]), "--sub-format", "best"]
            # Sponsorblock
            if config.get("sponsorblock"):
                cmd += ["--sponsorblock-mark", "all"]

            subprocess.run(cmd, check=True)
            return True
        except subprocess.CalledProcessError as e:
            st.warning(f"⚠️ Attempt {attempt} failed: {e.stderr.strip()[:200]}")
            if attempt == retries:
                st.error(f"❌ All {retries} attempts failed.")
                return False
    return False

# UI Starts here
st.title("📺 YouTube Playlist Downloader")

with st.sidebar:
    download_path = st.text_input("📁 Enter download path", value=config.get("download_path", str(Path.cwd())))
    mode = st.radio("Choose download mode", ["Download All", "Download One-by-One"], index=["Download All", "Download One-by-One"].index(config.get("mode", "Download All")))
    quality = st.selectbox("🎞️ Video Quality", ["best", "bestvideo+bestaudio", "worst", "worstvideo+worstaudio"], index=["best", "bestvideo+bestaudio", "worst", "worstvideo+worstaudio"].index(config.get("quality", "best")))
    thumbnail = st.checkbox("Download & Embed Thumbnail", value=config.get("thumbnail", True))
    metadata = st.checkbox("Download Metadata (info JSON)", value=config.get("metadata", True))
    sponsorblock = st.checkbox("SponsorBlock Marking", value=config.get("sponsorblock", True))
    subtitle_langs = st.multiselect("Subtitle Languages", ["en", "es", "fr", "de", "all"], default=config.get("subtitles", ["all"]))

    # Save config on any change
    config.update({
        "download_path": download_path,
        "mode": mode,
        "quality": quality,
        "thumbnail": thumbnail,
        "metadata": metadata,
        "sponsorblock": sponsorblock,
        "subtitles": subtitle_langs
    })
    save_config(config)

if not os.path.exists(download_path):
    st.warning("⚠️ Provided path does not exist.")
    st.stop()

st.markdown("### 🔗 Paste one or more YouTube playlist links below (one per line):")
user_input = st.text_area("Playlist URLs", height=150)

if st.button("Process URLs"):
    urls = list(set(filter(None, map(str.strip, user_input.splitlines()))))
    playlists = {}

    for url in urls:
        st.info(f"⏳ Processing: {url}")
        info = get_playlist_info(url)
        if info:
            playlists[url] = info

    if playlists:
        with open("playlist_data.json", "w", encoding="utf-8") as f:
            json.dump(playlists, f, indent=2, ensure_ascii=False)
        st.success("✅ All playlists processed and stored in `playlist_data.json`")

if os.path.exists("playlist_data.json"):
    with open("playlist_data.json", "r", encoding="utf-8") as f:
        playlists = json.load(f)

    for url, data in playlists.items():
        st.subheader(f"📁 Playlist: {data['title']}")
        with st.expander("🎞️ Show Episodes"):
            for i, vid in enumerate(data['videos']):
                st.markdown(f"**{i+1}. {vid['title']}**")

        if mode == "Download All":
            if st.button(f"⬇️ Download Entire Playlist: {data['title']}"):
                with st.status("Downloading...", expanded=True) as status:
                    progress = st.progress(0)
                    downloaded = 0
                    total = len(data['videos'])
                    for i, vid in enumerate(data['videos']):
                        st.write(f"▶ Downloading: {vid['title']}")
                        progress.progress((i + 1) / total)
                        if download_video(vid['id'], Path(download_path) / data['title'], config):
                            downloaded += 1
                    st.success(f"✅ Done! Downloaded {downloaded}/{len(data['videos'])} videos.")

        elif mode == "Download One-by-One":
            st.markdown("#### Choose Episode to Download:")
            for vid in data['videos']:
                if st.button(f"⬇️ {vid['title']}"):
                    with st.status("Downloading...", expanded=True) as status:
                        progress = st.progress(0)
                        success = download_video(vid['id'], Path(download_path) / data['title'], config)
                        if success:
                            st.success("✅ Downloaded successfully")
                        else:
                            st.error("❌ Failed to download")