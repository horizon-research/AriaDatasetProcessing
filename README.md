# 🎥 Aria VRS Video Processing Tools

This project provides tools for downloading and converting Aria VRS files.

Web: https://www.projectaria.com/datasets/aea/ 

Doc: https://facebookresearch.github.io/projectaria_tools/

## 📁 File Description

- `download_vrs.py`: VRS file download tool
- `create_video.py`: VRS to MP4 video conversion tool including Debayering , Devignetting, Undistortion, Rotation, Encode.
- `AriaEverydayActivities_download_urls.json`: JSON file containing download links. Including not only vrs, but also other data like slam results, gazes...
- `requirements.txt`: Python dependencies list


## 🚀 Environment Setup

### 1. Create Python Virtual Environment and Install Dependencies

```bash
# Create virtual environment
python3 -m venv env

# Activate virtual environment
source env/bin/activate

# Install required packages
pip install -r requirements.txt
```

Use ffmpeg for video re-encoding (recommended for better compression and preview):
```bash
sudo apt install ffmpeg
```

---

## 📖 Usage Guide

### 2. 📥 Download VRS Files

Use `download_vrs.py` to download `.vrs` files from a JSON file.

#### Basic Usage

```bash
# Download all .vrs files using default JSON file (AriaEverydayActivities_download_urls.json)
python download_vrs.py --outdir ./aria_downloads
```

#### Advanced Options

```bash
--json_path AriaEverydayActivities_download_urls.json # if you have custom json
--workers 4 # num of doewnload workers
--max-files 10 # download partial dataset, -1 = download all
```

---

### 3. 🎬 Convert VRS Files to Video

Use `create_video.py` to convert a single `.vrs` file to MP4 video.

#### Basic Usage

```bash
# Generate video from camera-rgb
python create_video.py --vrs xxx.vrs --output output.mp4 --camera camera-rgb
```

#### 📹 Select Different Cameras

```bash
# RGB camera (color, automatically rotated 90 degrees)
--camera camera-rgb
# SLAM left camera (grayscale)
--camera camera-slam-left
# SLAM right camera (grayscale)
--camera camera-slam-right
```

#### ⚙️ Video Parameter Adjustments

```bash
# Set output video FPS
--fps 60

# Process only first 300 frames (for testing)
--max_frames 300

# Adjust ffmpeg encoding quality (CRF value: 0-51, lower is better quality, default 23)
--crf 18

# Adjust encoding speed (preset: ultrafast, fast, medium, slow, veryslow)
# Slower presets produce smaller files but take longer to encode
--preset slow
```

#### 🌟 Devignetting Settings

```bash
# Change devignetting mask folder
--devignetting_mask ./dv_mask_bin
```

#### 🔧 Image Correction Parameters

```bash
# Adjust undistorted image resolution and field of view
--undist_w 1024 
--undist_h 1024 
--undist_fov 150
```


#### 🔄 Processing Pipeline

`create_video.py` processing pipeline includes the following steps:

1. **📂 Read VRS File**: Open .vrs file using `projectaria_tools`
2. **🎨 Debayering**: Convert raw Bayer pattern to RGB (*RGB camera only*, handled by data provider)
3. **✨ Devignetting**: Remove vignetting effect if mask folder is provided (applied by data provider)
4. **🔍 Image Undistortion**: Undistort each frame, converting fisheye to pinhole projection
5. **🔄 Image Rotation**: Rotate RGB camera images 90 degrees clockwise (*RGB camera only*)
6. **💾 Write Temp Video**: Write temp file using OpenCV FFV1 or MJPG codec
7. **🎞️ Re-encode**: Re-encode with ffmpeg using H.264 (libx264) to significantly reduce file size

**Note**: SLAM cameras (grayscale) skip step 2 and 5.

