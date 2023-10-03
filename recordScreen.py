import subprocess
import datetime
import time
import threading
# https://steam.oxxostudio.tw/category/python/library/threading.html
import os
from os import getpid
import json
import ctypes

import pyautogui
import numpy as np

import windrecorder.maintainManager as maintainManager
import windrecorder.utils as utils
from windrecorder.config import config
import windrecorder.files as files
import windrecorder.record as record

ffmpeg_path = 'ffmpeg'
video_path = config.record_videos_dir
user32 = ctypes.windll.User32

# 全局状态变量
monitor_change_rank = 0
last_screenshot_array = None
idle_maintain_time_gap = datetime.timedelta(hours=8)   # 与上次闲时维护至少相隔

try:
    # 读取之前闲时维护的时间
    with open("catch\\LAST_IDLE_MAINTAIN.MD", 'r', encoding='utf-8') as f:
        time_read = f.read()
        last_idle_maintain_time = datetime.datetime.strptime(time_read,"%Y-%m-%d_%H-%M-%S")
except:
    with open("catch\\LAST_IDLE_MAINTAIN.MD", 'w', encoding='utf-8') as f:
        f.write(str(datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")))
    last_idle_maintain_time = datetime.datetime.now()   # 上次闲时维护时间



# 判断是否已锁屏
def is_screen_locked():
    return user32.GetForegroundWindow() == 0

# 判断是否正在休眠
def is_system_awake():
    try:
        return user32.GetLastInputInfo() == 0
    except Exception:
        return True


# 索引文件
def index_video_data(video_saved_dir,vid_file_name):
    print("---\n---Indexing OCR data\n---")
    full_path = os.path.join(video_saved_dir,vid_file_name)
    if os.path.exists(full_path):
        print(f"--{full_path} existed. Start ocr processing.")
        maintainManager.ocr_process_single_video(video_saved_dir, vid_file_name, "catch\\i_frames")


# 录制屏幕
def record_screen(
        output_dir=config.record_videos_dir,
        target_res=config.target_screen_res,
        record_time=config.record_seconds
):
    """
    用ffmpeg持续录制屏幕,每15分钟保存一个视频文件
    """
    # 构建输出文件名 
    now = datetime.datetime.now()
    video_out_name = now.strftime("%Y-%m-%d_%H-%M-%S") + ".mp4"
    output_dir_with_date = now.strftime("%Y-%m") # 将视频存储在日期月份子目录下
    video_saved_dir = os.path.join(output_dir,output_dir_with_date)
    files.check_and_create_folder(video_saved_dir)
    out_path = os.path.join(video_saved_dir, video_out_name)

    if not os.path.exists(output_dir):
        os.mkdir(output_dir)

    # 获取屏幕分辨率并根据策略决定缩放
    screen_width, screen_height = utils.get_screen_resolution()
    target_scale_width, target_scale_height = record.get_scale_screen_res_strategy(screen_width, screen_height)
    print(f"Origin screen resolution: {screen_width}x{screen_height}, Resized to {target_scale_width}x{target_scale_height}.")

    ffmpeg_cmd = [
        ffmpeg_path,
        '-f', 'gdigrab',
        '-video_size', f"{screen_width}x{screen_height}",
        '-framerate', '2',
        '-i', 'desktop',
        '-vf', f'scale={target_scale_width}:{target_scale_height}',
        # 默认使用编码成 h264 格式
        '-c:v', 'libx264',
        # 默认码率为 200kbps
        '-b:v', '200k',
        '-bf', '8', '-g', '600', '-sc_threshold', '10',
        '-t', str(record_time), out_path
    ]

    # 执行命令        
    try:
        # 添加服务监测信息
        with open("catch\\LOCK_FILE_RECORD.MD", 'w', encoding='utf-8') as f:
            f.write(str(getpid()))
        print("---Start Recording via FFmpeg")
        # 运行ffmpeg
        subprocess.run(ffmpeg_cmd, check=True)
        return video_saved_dir, video_out_name
    except subprocess.CalledProcessError as ex:
        print(f"{ex.cmd} failed with return code {ex.returncode}")


# 持续录制屏幕的主要线程
def continuously_record_screen():
    global monitor_change_rank
    
    while not continuously_stop_event.is_set():
        # 主循环过程
        if monitor_change_rank > config.screentime_not_change_to_pause_record:
            print("屏幕内容没有更新，停止录屏中。进入闲时维护")
            subprocess.run('color 60', shell=True)

            # 算算是否该进入维护了（与上次维护时间相比）
            timegap_between_last_idle_maintain = datetime.datetime.now() - last_idle_maintain_time
            if timegap_between_last_idle_maintain > idle_maintain_time_gap:
                thread_idle_maintain = threading.Thread(target=idle_maintain_process)
                thread_idle_maintain.daemon = True  # 设置为守护线程
                thread_idle_maintain.start()

            time.sleep(10)
        else:
            subprocess.run('color 2f', shell=True)
            video_saved_dir, video_out_name = record_screen() # 录制屏幕
            screentime_detect_stop_event.wait(2)

            # 自动索引策略
            if config.OCR_index_strategy == 1:
                print(f"-Starting Indexing video data: '{video_out_name}'")
                thread_index_video_data = threading.Thread(target=index_video_data,args=(video_saved_dir,video_out_name,))
                thread_index_video_data.daemon = True  # 设置为守护线程
                thread_index_video_data.start()

            screentime_detect_stop_event.wait(2)
        

# 闲时维护的操作流程
def idle_maintain_process():
    print("idle_maintain")
    maintainManager.remove_outdated_videofiles()




# 测试ffmpeg是否存在可用
def test_ffmpeg():
    try:
        res = subprocess.run('ffmpeg -version')
    except Exception:
        print('Error: ffmpeg is not installed! Please ensure ffmpeg is in the PATH')
        exit(1)



# 每隔一段截图对比是否屏幕内容缺少变化
def monitor_compare_screenshot(screentime_detect_stop_event):
    while not screentime_detect_stop_event.is_set():
        if is_screen_locked() or not is_system_awake():
            print("Screen locked / System not awaked")
        else:
            try:
                global monitor_change_rank
                global last_screenshot_array
                similarity = None

                while(True):
                    screenshot = pyautogui.screenshot()
                    screenshot_array = np.array(screenshot)

                    if last_screenshot_array is not None:
                        similarity = maintainManager.compare_image_similarity_np(last_screenshot_array,screenshot_array)

                        if similarity > 0.9: #对比检测阈值
                            monitor_change_rank += 0.5
                        else:
                            monitor_change_rank = 0

                    last_screenshot_array = screenshot_array.copy()
                    print(f"----monitor_change_rank:{monitor_change_rank},similarity:{similarity}")
                    time.sleep(30)
            except Exception as e:
                print("--Error occurred:",str(e))
                monitor_change_rank = 0
        
        screentime_detect_stop_event.wait(5)



if __name__ == '__main__':
    if record.is_recording():
        print("Another screen record service is running.")
        exit(1)

    test_ffmpeg()
    print(f"-config.OCR_index_strategy: {config.OCR_index_strategy}")

    # 维护之前退出没留下的视频
    thread_maintain_last_time = threading.Thread(target=maintainManager.maintain_manager_main)
    thread_maintain_last_time.start()

    # 屏幕内容多长时间不变则暂停录制
    print(f"-config.screentime_not_change_to_pause_record:{config.screentime_not_change_to_pause_record}")
    screentime_detect_stop_event = threading.Event() # 使用事件对象来检测检测函数是否意外被终止
    if config.screentime_not_change_to_pause_record >0:   # 是否使用屏幕不变检测
        thread_monitor_compare_screenshot = threading.Thread(target=monitor_compare_screenshot,args=(screentime_detect_stop_event,))
        thread_monitor_compare_screenshot.start()
    else:
        monitor_change_rank = -1

    #录屏的线程
    continuously_stop_event = threading.Event()
    thread_continuously_record_screen = threading.Thread(target=continuously_record_screen)
    thread_continuously_record_screen.start()


    while(True):
        # # 主循环过程
        # if monitor_change_rank > config.screentime_not_change_to_pause_record:
        #     print("屏幕内容没有更新，停止录屏中。进入闲时维护")
        #     time.sleep(10)
        # else:
        #     video_saved_dir, video_out_name = record_screen() # 录制屏幕
        #     time.sleep(2) # 歇口气
        #     # 自动索引策略
        #     if config.OCR_index_strategy == 1:
        #         print(f"-Starting Indexing video data: '{video_out_name}'")
        #         thread_index_video_data = threading.Thread(target=index_video_data,args=(video_saved_dir,video_out_name,))
        #         thread_index_video_data.daemon = True  # 设置为守护线程
        #         thread_index_video_data.start()
        #     time.sleep(2) # 再歇
        
        # 如果屏幕检测线程意外出错，重启它
        if not thread_monitor_compare_screenshot.is_alive() and config.screentime_not_change_to_pause_record >0:
            thread_monitor_compare_screenshot = threading.Thread(target=monitor_compare_screenshot)
            thread_monitor_compare_screenshot.start()
        
        # 如果屏幕录制线程意外出错，重启它
        if not thread_continuously_record_screen.is_alive():
            thread_continuously_record_screen = threading.Thread(target=continuously_record_screen)
            thread_continuously_record_screen.start()
        
        time.sleep(30)
