import os
import subprocess
import shutil
import re
import sys

# ================= 配置区域 =================

# 视频后缀 (不区分大小写)
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.mkv', '.avi', '.mts', '.m4v'}

# 音频码率上限（kbps）
MAX_AUDIO_KBPS = 192
DEFAULT_AUDIO_KBPS = 192

# ===========================================

def run_applescript(script):
    """运行 AppleScript 并返回结果"""
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None

def parse_duration(duration_str):
    """将时间字符串转换为秒数，例如 '00:01:30.45' -> 90.45"""
    try:
        parts = duration_str.split(':')
        hours = float(parts[0])
        minutes = float(parts[1])
        seconds = float(parts[2])
        return hours * 3600 + minutes * 60 + seconds
    except (AttributeError, IndexError, ValueError, TypeError):
        return 0

def get_audio_bitrate_kbps(input_path):
    """获取源文件音频码率（kbps），失败返回 None"""
    if shutil.which("ffprobe") is None:
        return None
    try:
        result = subprocess.run(
            [
                'ffprobe', '-v', 'error',
                '-select_streams', 'a:0',
                '-show_entries', 'stream=bit_rate',
                '-of', 'default=nk=1:nw=1',
                input_path
            ],
            capture_output=True,
            text=True,
            check=True
        )
        raw = result.stdout.strip()
        if not raw.isdigit():
            return None
        return max(1, int(round(int(raw) / 1000)))
    except (subprocess.CalledProcessError, OSError, ValueError):
        return None

def has_ffmpeg_encoder(encoder_name):
    """检查 ffmpeg 是否支持指定编码器"""
    try:
        result = subprocess.run(
            ['ffmpeg', '-hide_banner', '-encoders'],
            capture_output=True,
            text=True,
            check=True
        )
        return encoder_name in result.stdout
    except (subprocess.CalledProcessError, OSError):
        return False

def run_ffmpeg_with_progress(cmd):
    """运行 FFmpeg 并显示进度"""
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        bufsize=1,
        errors="replace"
    )
    
    duration = None
    
    # 读取 stderr 来获取进度信息（FFmpeg 将进度输出到 stderr）
    for line in process.stderr:
        # 提取总时长
        if duration is None:
            duration_match = re.search(r'Duration: (\d{2}:\d{2}:\d{2}\.\d{2})', line)
            if duration_match:
                duration = parse_duration(duration_match.group(1))
        
        # 提取当前进度
        time_match = re.search(r'time=(\d{2}:\d{2}:\d{2}\.\d{2})', line)
        if time_match and duration and duration > 0:
            current_time = parse_duration(time_match.group(1))
            progress = min(100, (current_time / duration) * 100)
            
            # 使用 \r 在同一行更新进度
            sys.stdout.write(f'\r   进度: {progress:.1f}% ')
            sys.stdout.flush()
    
    # 等待进程完成
    process.wait()
    
    # 换行，避免后续输出覆盖进度条
    if duration:
        sys.stdout.write('\n')
        sys.stdout.flush()
    
    # 如果返回码不为 0，抛出异常
    if process.returncode != 0:
        raise subprocess.CalledProcessError(process.returncode, cmd)
    
    return process.returncode

def get_user_input():
    """
    使用 macOS 原生对话框获取用户输入的源文件夹、质量参数、编码格式、位深和编码模式
    """
    print("正在打开文件夹选择窗口...")
    
    # 1. 选择文件夹
    # choose folder 返回的是 HFS 路径 (Macintosh HD:Users:...), 需要转为 POSIX 路径
    folder_script = 'POSIX path of (choose folder with prompt "请选择包含视频的文件夹")'
    source_dir = run_applescript(folder_script)
    
    if not source_dir:
        print("❌ 用户取消或未选择文件夹，程序退出。")
        return None, None, None, None, None

    # 2. 选择编码格式
    codec_script = 'button returned of (display dialog "请选择视频编码格式:\n\nH.264: 兼容性最好，几乎所有设备支持\nH.265: 压缩率更高，文件更小" buttons {"H.264", "H.265"} default button "H.265" with title "编码格式选择")'
    codec_choice = run_applescript(codec_script)
    
    if not codec_choice:
        print("❌ 用户取消或未选择编码格式，程序退出。")
        return None, None, None, None, None

    # 3. 选择编码模式
    mode_script = 'button returned of (display dialog "请选择编码模式:\n\n软件编码: 质量控制更精细，速度较慢\n硬件加速(VideoToolbox): 速度更快，更省电（质量控制较粗）" buttons {"软件编码", "硬件加速"} default button "硬件加速" with title "编码模式选择")'
    mode_choice = run_applescript(mode_script)

    if not mode_choice:
        print("❌ 用户取消或未选择编码模式，程序退出。")
        return None, None, None, None, None

    # 4. 选择位深
    bit_depth_script = 'button returned of (display dialog "请选择输出位深:\n\n8-bit: 兼容性最好，默认推荐\n10-bit: 色彩过渡更平滑，压缩效率通常更好（兼容性较低）" buttons {"8-bit", "10-bit"} default button "8-bit" with title "位深选择")'
    bit_depth_choice = run_applescript(bit_depth_script)

    if not bit_depth_choice:
        print("❌ 用户取消或未选择位深，程序退出。")
        return None, None, None, None, None

    # H.264 的 VideoToolbox 不支持 10-bit，提前拦截
    if mode_choice == "硬件加速" and codec_choice == "H.264" and bit_depth_choice == "10-bit":
        print("❌ H.264 硬件加速不支持 10-bit。请改用 H.265 或 8-bit。")
        return None, None, None, None, None

    # 5. 获取质量参数
    if mode_choice == "软件编码":
        quality_script = 'text returned of (display dialog "请输入 CRF (0-51，推荐 18-30)" default answer "23" buttons {"OK"} default button "OK" with title "视频质量设置")'
    else:
        quality_script = 'text returned of (display dialog "请输入硬件编码质量值 q:v (1-100，推荐 65，越大越清晰)" default answer "65" buttons {"OK"} default button "OK" with title "视频质量设置")'
    quality = run_applescript(quality_script)
    
    if not quality:
        print("❌ 用户取消或未输入质量，程序退出。")
        return None, None, None, None, None

    # 验证输入范围
    if not quality.isdigit():
        print(f"❌ 输入的质量值 '{quality}' 无效，请输入数字。")
        return None, None, None, None, None
    quality_value = int(quality)
    if mode_choice == "软件编码" and not (0 <= quality_value <= 51):
        print(f"❌ 输入的 CRF '{quality}' 无效，请输入 0-51 之间的数字。")
        return None, None, None, None, None
    if mode_choice == "硬件加速" and not (1 <= quality_value <= 100):
        print(f"❌ 输入的 q:v '{quality}' 无效，请输入 1-100 之间的数字。")
        return None, None, None, None, None

    return source_dir, quality, codec_choice, bit_depth_choice, mode_choice

def compress_videos(source_dir, quality, codec_choice, bit_depth_choice, mode_choice):
    # 检查 FFmpeg 是否安装
    if shutil.which("ffmpeg") is None:
        print("错误：未检测到 FFmpeg。请先安装 FFmpeg (brew install ffmpeg)")
        return

    # 规范化路径，避免 source_dir 带尾斜杠时 basename 为空
    source_dir = os.path.normpath(source_dir)

    # 自动生成输出文件夹路径：在源文件夹旁边创建一个 _compressed 后缀的文件夹
    parent_dir = os.path.dirname(source_dir)
    folder_name = os.path.basename(source_dir)
    target_dir = os.path.join(parent_dir, f"{folder_name}_compressed")

    # 根据用户选择设置编码参数
    if codec_choice == "H.264":
        video_tag = 'avc1'
        if mode_choice == "硬件加速":
            video_codec = 'h264_videotoolbox'
            codec_name = 'H.264 (VideoToolbox)'
        else:
            video_codec = 'libx264'
            codec_name = 'H.264 (Software, CRF)'
    else:  # H.265
        video_tag = 'hvc1'
        if mode_choice == "硬件加速":
            video_codec = 'hevc_videotoolbox'
            codec_name = 'H.265 (VideoToolbox)'
        else:
            video_codec = 'libx265'
            codec_name = 'H.265 (Software, CRF)'

    if not has_ffmpeg_encoder(video_codec):
        print(f"错误：当前 FFmpeg 不支持编码器 '{video_codec}'。")
        print("请安装支持该编码器的 FFmpeg，或切换为软件编码。")
        return

    is_10bit = bit_depth_choice == "10-bit"
    pix_fmt = 'yuv420p10le' if is_10bit else 'yuv420p'
    bit_depth_name = "10-bit" if is_10bit else "8-bit"
    quality_label = "CRF" if mode_choice == "软件编码" else "q:v"

    print(f"🚀 开始扫描目录: {source_dir}")
    print(f"📂 目标保存目录: {target_dir}")
    print(f"⚙️ 编码模式: {mode_choice}")
    print(f"🎬 视频编码格式: {codec_name}")
    print(f"🧩 输出位深: {bit_depth_name}")
    print(f"🎨 视频质量设置 ({quality_label}): {quality}")
    print("-" * 50)
    
    # 统计信息
    stats = {
        'discovered_videos': 0,      # 发现的视频总数
        'total_videos': 0,           # 处理的视频总数
        'compressed_videos': 0,      # 实际压缩的视频数
        'copied_videos': 0,          # 复制原文件的视频数（压缩后变大）
        'skipped_videos': 0,         # 跳过的视频（已存在）
        'total_files': 0,            # 处理的非视频文件总数
        'skipped_files': 0,          # 跳过的非视频文件
        'original_size': 0,          # 原始总大小（字节）
        'final_size': 0,             # 最终总大小（字节）
        'failed_videos': 0,          # 转码失败的视频数
    }

    # 遍历源目录
    for root, dirs, files in os.walk(source_dir):
        for file in files:
            # 忽略 Mac 系统产生的隐藏文件 (如 ._video.mp4)
            if file.startswith("._") or file.startswith(".DS_Store"):
                continue

            file_ext = os.path.splitext(file)[1].lower()
            
            # 构建完整的源文件路径
            input_path = os.path.join(root, file)
            
            # 构建输出文件的相对路径
            relative_path = os.path.relpath(root, source_dir)
            target_folder = os.path.join(target_dir, relative_path)
            
            # 确保目标子文件夹存在
            if not os.path.exists(target_folder):
                os.makedirs(target_folder)
            
            if file_ext in VIDEO_EXTENSIONS:
                # 处理视频文件：转码
                stats['discovered_videos'] += 1
                
                # 构建输出文件名 (统一改为 .mp4 以获得最佳兼容性)
                output_filename = os.path.splitext(file)[0] + ".mp4"
                output_path = os.path.join(target_folder, output_filename)

                # 如果目标文件已存在，则跳过
                if os.path.exists(output_path):
                    print(f"⏭️  已存在，跳过: {output_filename}")
                    stats['skipped_videos'] += 1
                    continue

                print(f"🎬 正在转码: {file}")

                # 根据源音频码率做上限限制，避免高码率被强压到 128k
                src_audio_kbps = get_audio_bitrate_kbps(input_path)
                if src_audio_kbps is None:
                    target_audio_kbps = DEFAULT_AUDIO_KBPS
                else:
                    target_audio_kbps = min(MAX_AUDIO_KBPS, src_audio_kbps)

                # 临时文件路径
                temp_output_path = output_path + ".temp.mp4"

                # FFmpeg 命令 (输出到临时文件)
                # 软件编码参数
                cmd = [
                    'ffmpeg',
                    '-i', input_path,
                    '-c:v', video_codec,            # 使用用户选择的编码器
                    '-tag:v', video_tag,            # 对应的视频标签 (avc1 for H.264, hvc1 for H.265)
                    '-pix_fmt', pix_fmt,            # 8-bit/10-bit 输出
                    '-c:a', 'aac',                  # 音频编码
                    '-b:a', f'{target_audio_kbps}k',# 音频码率（上下限限制）
                    '-movflags', '+faststart',      # 将 moov 头移到文件前部，提升兼容性
                    '-y'                            # 覆盖确认
                ]

                if mode_choice == "软件编码":
                    cmd.extend(['-crf', quality])   # 软件编码使用 CRF
                    # H.264 10-bit 需要明确 high10 profile 以避免协商到 8-bit
                    if video_codec == 'libx264' and is_10bit:
                        cmd.extend(['-profile:v', 'high10'])

                elif mode_choice == "硬件加速":
                    cmd.extend(['-q:v', quality])   # VideoToolbox 使用 q:v

                # 软件编码的性能参数
                if video_codec == 'libx264':
                    cmd.extend([
                        '-preset', 'medium',        # 编码预设 (ultrafast, superfast, veryfast, faster, fast, medium, slow, slower, veryslow)
                        '-threads', '0'             # 自动选择线程数
                    ])
                # 如果是 libx265 编码器，添加针对多核 CPU 的优化参数
                elif video_codec == 'libx265':
                    cmd.extend([
                        '-preset', 'medium',        # 编码预设
                        '-threads', '0'             # 自动选择线程数
                    ])

                # 输出文件路径放在最后，确保前面的输出参数生效
                cmd.append(temp_output_path)

                try:
                    run_ffmpeg_with_progress(cmd)
                    
                    # 获取文件大小 (字节)
                    src_size = os.path.getsize(input_path)
                    out_size = os.path.getsize(temp_output_path)
                    
                    stats['total_videos'] += 1
                    
                    # 防膨胀逻辑：如果压缩后变大，则保留原文件
                    if out_size >= src_size:
                        print(f"⚠️  压缩后体积变大 ({out_size/1024/1024:.2f}MB > {src_size/1024/1024:.2f}MB)，保留原画。")
                        os.remove(temp_output_path) # 删除较大的压缩文件
                        shutil.copy2(input_path, output_path) # 复制原文件
                        print(f"✅ 已复制原文件: {output_filename}")
                        stats['copied_videos'] += 1
                        stats['original_size'] += src_size
                        stats['final_size'] += src_size
                    else:
                        # 压缩有效，重命名临时文件为正式文件
                        os.rename(temp_output_path, output_path)
                        stats['compressed_videos'] += 1
                        stats['original_size'] += src_size
                        stats['final_size'] += out_size
                        reduction = (src_size - out_size) / src_size * 100
                        print(f"✅ 转码完成: {output_filename} (体积减小 {reduction:.1f}%)")

                except subprocess.CalledProcessError as e:
                    stats['failed_videos'] += 1
                    print(f"❌ 转码失败: {file} \n错误信息: {e}")
                    if os.path.exists(temp_output_path):
                        os.remove(temp_output_path)
            
            else:
                # 处理非视频文件：直接复制
                output_path = os.path.join(target_folder, file)
                
                # 如果目标文件已存在，则跳过
                if os.path.exists(output_path):
                    print(f"⏭️  已存在，跳过: {file}")
                    stats['skipped_files'] += 1
                    continue
                
                try:
                    shutil.copy2(input_path, output_path)
                    print(f"📄 已复制: {file}")
                    stats['total_files'] += 1
                    file_size = os.path.getsize(input_path)
                    stats['original_size'] += file_size
                    stats['final_size'] += file_size
                except Exception as e:
                    print(f"❌ 复制失败: {file} \n错误信息: {e}")

    print("-" * 50)
    print("🎉 所有任务处理完毕！")
    print()
    
    # 显示统计信息
    print("=" * 50)
    print("📊 处理统计报告")
    print("=" * 50)
    
    # 视频统计
    total_video_operations = stats['discovered_videos']
    if total_video_operations > 0:
        print(f"\n🎬 视频文件:")
        print(f"   总共发现: {total_video_operations} 个")
        print(f"   ✅ 成功压缩: {stats['compressed_videos']} 个")
        print(f"   📋 保留原文件: {stats['copied_videos']} 个 (压缩后反而变大)")
        print(f"   ⏭️  跳过已存在: {stats['skipped_videos']} 个")
        if stats['failed_videos'] > 0:
            print(f"   ❌ 转码失败: {stats['failed_videos']} 个")
    
    # 其他文件统计
    total_other_files = stats['total_files'] + stats['skipped_files']
    if total_other_files > 0:
        print(f"\n📄 其他文件:")
        print(f"   总共发现: {total_other_files} 个")
        print(f"   ✅ 已复制: {stats['total_files']} 个")
        print(f"   ⏭️  跳过已存在: {stats['skipped_files']} 个")
    
    # 容量统计
    if stats['original_size'] > 0:
        print(f"\n💾 存储空间:")
        original_mb = stats['original_size'] / 1024 / 1024
        final_mb = stats['final_size'] / 1024 / 1024
        saved_mb = (stats['original_size'] - stats['final_size']) / 1024 / 1024
        
        # 选择合适的单位显示
        if original_mb >= 1024:
            original_gb = original_mb / 1024
            final_gb = final_mb / 1024
            saved_gb = saved_mb / 1024
            print(f"   原始大小: {original_gb:.2f} GB")
            print(f"   压缩后大小: {final_gb:.2f} GB")
            print(f"   节省空间: {saved_gb:.2f} GB")
        else:
            print(f"   原始大小: {original_mb:.2f} MB")
            print(f"   压缩后大小: {final_mb:.2f} MB")
            print(f"   节省空间: {saved_mb:.2f} MB")
        
        # 计算压缩率
        if stats['original_size'] != stats['final_size']:
            compression_ratio = (1 - stats['final_size'] / stats['original_size']) * 100
            if compression_ratio > 0:
                print(f"   📉 总体压缩率: {compression_ratio:.1f}%")
            else:
                print(f"   📈 总体膨胀率: {abs(compression_ratio):.1f}%")
        else:
            print(f"   ➡️  大小未变化")
    
    print("\n" + "=" * 50)

if __name__ == "__main__":
    source_dir, quality, codec_choice, bit_depth_choice, mode_choice = get_user_input()
    if source_dir and quality and codec_choice and bit_depth_choice and mode_choice:
        compress_videos(source_dir, quality, codec_choice, bit_depth_choice, mode_choice)
