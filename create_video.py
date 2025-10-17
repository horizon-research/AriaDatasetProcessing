#!/usr/bin/env python3
"""
Generate video from Aria .vrs file with undistortion and rotation.
Writes with OpenCV, then re-encodes with ffmpeg for better compression.

Usage:
  python create_video.py --vrs 792287.vrs --output output.mp4 --camera camera-rgb
"""

import argparse
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np
from projectaria_tools.core import calibration, data_provider, image
from tqdm import tqdm


def get_provider(vrs_path: Path, devignetting_mask_folder: Path = None):
    prov = data_provider.create_vrs_data_provider(str(vrs_path))
    if prov is None:
        raise RuntimeError(f"Failed to open VRS file: {vrs_path}")
    
    # Set up devignetting if mask folder is provided
    if devignetting_mask_folder is not None and devignetting_mask_folder.exists():
        print(f"[Setup] Setting devignetting mask folder: {devignetting_mask_folder}")
        prov.set_devignetting_mask_folder_path(str(devignetting_mask_folder))
        prov.set_devignetting(True)
        print(f"[Setup] Devignetting enabled")
    else:
        if devignetting_mask_folder is not None:
            print(f"[Setup][WARN] Devignetting mask folder not found: {devignetting_mask_folder}")
    
    return prov


def process_frame_rgb_rotated(prov, cam_label: str, index: int, undist_w: int, undist_h: int, undist_fov_deg: float):
    """
    Process a single frame: undistort camera-rgb -> rotate CW90.
    Returns the processed frame as uint8 RGB array.
    """
    try:
        sid = prov.get_stream_id_from_label(cam_label)
        img_data = prov.get_image_data_by_index(sid, index)
        if img_data is None or len(img_data) == 0:
            return None
        
        raw = img_data[0].to_numpy_array()
        
        # Get calibration
        device_calib = prov.get_device_calibration().get_camera_calib(cam_label)
        
        # Create pinhole with device->camera transform
        pinhole = calibration.get_linear_camera_calibration(
            undist_w, undist_h, undist_fov_deg, cam_label, device_calib.get_transform_device_camera()
        )

        # import ipdb; ipdb.set_trace()
        
        
        # Undistort
        undist = calibration.distort_by_calibration(raw, pinhole, device_calib)
        
        # Rotate CW90: np.rot90 with k=3
        rotated = np.rot90(undist, k=3)
        
        # Ensure uint8
        if rotated.dtype != np.uint8:
            rotated = rotated.astype(np.uint8)
        
        return rotated
    except Exception as e:
        print(f"Error processing frame {index}: {e}", file=sys.stderr)
        return None


def process_frame_slam_left(prov, cam_label: str, index: int, undist_w: int, undist_h: int, undist_fov_deg: float):
    """
    Process a single frame: undistort camera-slam-left.
    Returns the processed frame as uint8 grayscale array.
    """
    try:
        sid = prov.get_stream_id_from_label(cam_label)
        img_data = prov.get_image_data_by_index(sid, index)
        if img_data is None or len(img_data) == 0:
            return None
        
        raw = img_data[0].to_numpy_array()
        
        # Get calibration
        device_calib = prov.get_device_calibration().get_camera_calib(cam_label)
        
        # Create pinhole
        pinhole = calibration.get_linear_camera_calibration(undist_w, undist_h, undist_fov_deg)
        
        # Undistort
        undist = calibration.distort_by_calibration(raw, pinhole, device_calib)
        
        # Ensure uint8
        if undist.dtype != np.uint8:
            undist = undist.astype(np.uint8)
        
        return undist
    except Exception as e:
        print(f"Error processing frame {index}: {e}", file=sys.stderr)
        return None


def create_video_opencv(prov, cam_label: str, output_path: Path, undist_w: int, undist_h: int, 
                       undist_fov_deg: float, fps: float = 30.0, max_frames: int = None):
    """
    Create video using OpenCV VideoWriter.
    """
    print(f"Creating video from {cam_label}...")
    
    # Get stream info
    sid = prov.get_stream_id_from_label(cam_label)
    num_frames = prov.get_num_data(sid)
    
    if max_frames is not None:
        num_frames = min(num_frames, max_frames)
    
    print(f"Total frames to process: {num_frames}")
    
    # Process first frame to get dimensions
    is_rgb = "rgb" in cam_label.lower()
    if is_rgb:
        first_frame = process_frame_rgb_rotated(prov, cam_label, 0, undist_w, undist_h, undist_fov_deg)
    else:
        first_frame = process_frame_slam_left(prov, cam_label, 0, undist_w, undist_h, undist_fov_deg)
    
    if first_frame is None:
        raise RuntimeError("Failed to process first frame")
    
    height, width = first_frame.shape[:2]
    print(f"Video dimensions: {width}x{height}")
    
    # Create VideoWriter with raw codec (uncompressed or lossless)
    # Use 'FFV1' for lossless, or 'MJPG' for motion jpeg
    fourcc = cv2.VideoWriter_fourcc(*'FFV1')  # Lossless codec
    
    # For RGB, use color; for grayscale, convert to BGR
    is_color = len(first_frame.shape) == 3 and first_frame.shape[2] == 3
    
    writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height), isColor=is_color)
    
    if not writer.isOpened():
        # Fallback to MJPG if FFV1 doesn't work
        print("FFV1 codec not available, falling back to MJPG...")
        fourcc = cv2.VideoWriter_fourcc(*'MJPG')
        writer = cv2.VideoWriter(str(output_path), fourcc, fps, (width, height), isColor=is_color)
    
    if not writer.isOpened():
        raise RuntimeError(f"Failed to create video writer for {output_path}")
    
    # Write frames
    for idx in tqdm(range(num_frames), desc="Writing frames"):
        if is_rgb:
            frame = process_frame_rgb_rotated(prov, cam_label, idx, undist_w, undist_h, undist_fov_deg)
        else:
            frame = process_frame_slam_left(prov, cam_label, idx, undist_w, undist_h, undist_fov_deg)
        
        if frame is None:
            print(f"Warning: Skipping frame {idx}")
            continue
        
        # OpenCV expects BGR, so convert RGB to BGR if needed
        if is_color:
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
            writer.write(frame_bgr)
        else:
            # For grayscale, convert to BGR
            frame_bgr = cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)
            writer.write(frame_bgr)
    
    writer.release()
    print(f"OpenCV video saved to {output_path}")


def reencode_with_ffmpeg(input_path: Path, output_path: Path, crf: int = 23, preset: str = "medium"):
    """
    Re-encode video with ffmpeg for better compression.
    
    Args:
        input_path: Input video path
        output_path: Output video path
        crf: Constant Rate Factor (0-51, lower = better quality, 23 is default)
        preset: Encoding preset (ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow)
    """
    print(f"\nRe-encoding with ffmpeg (crf={crf}, preset={preset})...")
    
    cmd = [
        "ffmpeg",
        "-i", str(input_path),
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", preset,
        "-pix_fmt", "yuv420p",
        "-y",  # Overwrite output file
        str(output_path)
    ]
    
    try:
        subprocess.run(cmd, check=True)
        print(f"Re-encoded video saved to {output_path}")
        
        # Show file sizes
        original_size = input_path.stat().st_size / (1024 * 1024)  # MB
        new_size = output_path.stat().st_size / (1024 * 1024)  # MB
        print(f"\nFile sizes:")
        print(f"  Original (OpenCV): {original_size:.2f} MB")
        print(f"  Re-encoded (ffmpeg): {new_size:.2f} MB")
        print(f"  Compression ratio: {original_size/new_size:.2f}x")
        
    except subprocess.CalledProcessError as e:
        print(f"Error running ffmpeg: {e}", file=sys.stderr)
        raise


def main():
    parser = argparse.ArgumentParser(description="Generate video from Aria .vrs with undistortion")
    parser.add_argument("--vrs", type=Path, default="792287.vrs", help="Path to input .vrs file")
    parser.add_argument("--output", type=Path, default="output_final.mp4", help="Final output video path")
    parser.add_argument("--camera", type=str, default="camera-rgb", 
                       choices=["camera-rgb", "camera-slam-left", "camera-slam-right"],
                       help="Camera stream to process")
    
    # Video parameters
    parser.add_argument("--fps", type=float, default=20.0, help="Output video FPS")
    parser.add_argument("--max_frames", type=int, default=None, help="Maximum number of frames to process")
    
    # Undistortion parameters
    parser.add_argument("--undist_w", type=int, default=1408, help="Undistorted image width")
    parser.add_argument("--undist_h", type=int, default=1408, help="Undistorted image height")
    parser.add_argument("--undist_fov", type=float, default=150.0, help="Undistorted FOV in degrees")
    
    # Devignetting
    parser.add_argument("--devignetting_mask", type=Path, default="dv_mask_bin", 
                       help="Path to devignetting mask folder (e.g., dv_mask_bin)")
    
    # FFmpeg parameters
    parser.add_argument("--crf", type=int, default=23, help="FFmpeg CRF value (0-51, lower=better)")
    parser.add_argument("--preset", type=str, default="medium", 
                       choices=["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"],
                       help="FFmpeg encoding preset")
    parser.add_argument("--keep_temp", action="store_true", help="Keep temporary OpenCV video file")
    parser.add_argument("--no_ffmpeg", action="store_true", help="Skip ffmpeg re-encoding (keep OpenCV output only)")
    
    args = parser.parse_args()
    
    # hacks to fix bug
    args.undist_fov = 150 * args.undist_w / 512


    # Create provider
    prov = get_provider(args.vrs, args.devignetting_mask)
    
    # Decide output strategy based on ffmpeg availability
    if args.no_ffmpeg:
        # Direct output without ffmpeg
        final_output = args.output
        print("Skipping ffmpeg re-encoding (--no_ffmpeg specified)")
    else:
        # Use temporary file for OpenCV, then re-encode
        final_output = args.output.parent / f"{args.output.stem}_temp.avi"
    
    # Step 1: Create video with OpenCV
    create_video_opencv(
        prov, 
        args.camera, 
        final_output,
        args.undist_w,
        args.undist_h,
        args.undist_fov,
        args.fps,
        args.max_frames
    )
    
    # Step 2: Re-encode with ffmpeg (if not skipped)
    if not args.no_ffmpeg:
        try:
            reencode_with_ffmpeg(final_output, args.output, args.crf, args.preset)
            
            # Clean up temporary file
            if not args.keep_temp:
                print(f"\nRemoving temporary file {final_output}...")
                final_output.unlink()
            
            print(f"\n✓ Done! Final video: {args.output}")
        except FileNotFoundError:
            print("\n[WARN] ffmpeg not found. Keeping OpenCV output only.")
            print(f"      To install ffmpeg: sudo apt install ffmpeg")
            if final_output != args.output:
                print(f"\nRenaming {final_output} -> {args.output}")
                final_output.rename(args.output)
            print(f"\n✓ Done! Video saved: {args.output}")
    else:
        print(f"\n✓ Done! Video saved: {args.output}")


if __name__ == "__main__":
    main()

