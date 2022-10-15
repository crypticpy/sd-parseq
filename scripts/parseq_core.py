import string
import numpy as np
from tqdm import trange
import glob
import os
import subprocess
from PIL import Image, ImageDraw
import logging
import ffmpeg
import json
import cv2
from math import pi
import os
import sys
from subprocess import Popen, PIPE
from skimage import exposure
import math

class Parseq():

    def run(self, p, input_img, input_path:string, output_path:string, param_script_string:string, cc_window_size:int, cc_window_rate:float, sd_processor):
        # TODO - batch count & size support (only useful is seed is random)
        # TODO - seed travelling

        # Inputs:
        # - blank: Single-image loopback
        # - directory: assume directory of images
        # - file: assume video

        # Load param_script
        param_script = load_param_script(param_script_string)
        param_script_frames= max(param_script.keys())
        
        # Get input frame info (TODO: other input types)
        input_frames = video_frames(input_path)
        input_width = video_width(input_path)
        input_height = video_height(input_path)
        input_fps = video_fps(input_path)
        logging.info(f"input: {input_frames} frames ({input_width}x{input_height} @ {input_fps})")

        # Compare input frame count to scripted frame count
        logging.info(f"Script frames: {param_script_frames}, input frames: {input_frames}")
        frame_ratio = param_script_frames / float(input_frames)
        if frame_ratio < 1:
            logging.warning(f"Some input frames will be skipped to match script frame count. Ratio: {frame_ratio}")
        elif frame_ratio > 1:
            logging.warning(f"Some input frames will be duplicated to match script frame count. Ratio: {frame_ratio}")

        # Init video in/out
        process1 = (
            ffmpeg
            .input(input_path)
            .output('pipe:', format='rawvideo', pix_fmt='rgb24', r=input_fps)
            .run_async(pipe_stdout=True)
        )

        video =ffmpeg.input('pipe:', framerate=input_fps, format='rawvideo', pix_fmt='rgb24', s='{}x{}'.format(p.width, p.height))
        audio =ffmpeg.input(input_path)
        process2 = (
            ffmpeg
            .output(video, output_path, pix_fmt='yuv420p', r=input_fps)
            .overwrite_output()
            .run_async(pipe_stdin=True)
        )

        frame_pos = 0
        out_frame_history=[]
        while True:
            if not frame_pos in param_script:
                logging.info(f"Ending: no script information about how to process frame {frame_pos}.")
                break     

            # Read frame
            ##### if from video
            in_bytes = process1.stdout.read(input_width * input_height * 3)
            if not in_bytes:
                logging.info(f"Ending: no further video input at frame {frame_pos}.")
                break            
            in_frame = (
                np
                .frombuffer(in_bytes, np.uint8)
                .reshape([input_height, input_width, 3])
            )
            if frame_pos == 0:
                initial_input_image = in_frame.copy()
            ##### if from loopback                  
            #if frame_pos == 0:
                # in_frame = input_img
                # initial_input_image = input_img
            #else
                # in_frame = out_frame_history[frame_pos-1]
                

            # Resize
            in_frame_resized = cv2.resize(in_frame, (p.width, p.height), interpolation = cv2.INTER_LANCZOS4)
           
            #Rotate, zoom & pan (x,y,z)
            in_frame_rotated = ImageTransformer().rotate_along_axis(in_frame_resized, param_script[frame_pos]['rotx'], param_script[frame_pos]['roty'], param_script[frame_pos]['rotz'],
               -param_script[frame_pos]['panx'], -param_script[frame_pos]['pany'], -param_script[frame_pos]['zoom'])

            # Blend historical frames
            start_frame_pos = round(clamp(0, frame_pos-param_script[frame_pos]['loopback_frames'], len(out_frame_history)-1))
            end_frame_pos = round(clamp(0, frame_pos-1, len(out_frame_history)-1))
            frames_to_blend = [in_frame_rotated] + out_frame_history[start_frame_pos:end_frame_pos]
            blend_decay = clamp(0.1, param_script[frame_pos]['loopback_decay'], 1)
            logging.debug(f"Blending {len(frames_to_blend)} frames (current frame plus {start_frame_pos} to {end_frame_pos}) with decay {blend_decay}.")
            in_frame_blended = self.blend_frames(frames_to_blend, blend_decay)
            
            # Do SD 
            # TODO - batch count & batch size support: for each batch, for each batch_item          
            # TODO - prompt morphing: craft weighted prompt object - can be done in client
            # TODO - seed travelling based on decimal seeds?
            p.n_iter = 1
            p.batch_size = 1
            p.init_images = [Image.fromarray(in_frame_blended)] 
            p.seed = math.floor(param_script[frame_pos]['seed'])
            p.subseed = param_script[frame_pos]['subseed']
            p.subseed_strength = param_script[frame_pos]['subseed_strength']

            p.scale = clamp(-100, param_script[frame_pos]['scale'], 100)
            p.denoising_strength = clamp(0.01, param_script[frame_pos]['denoise'], 1)
            p.prompt = param_script[frame_pos]['positive_prompt'] 
            p.negative_prompt = param_script[frame_pos]['negative_prompt'] 
            
            logging.info(f"[{frame_pos}] - seed:{p.seed}; subseed:{p.subseed}; subseed_strength:{p.subseed_strength}; scale:{p.scale}; ds:{p.denoising_strength}; prompt: {p.prompt}; negative_prompt: {p.negative_prompt}")
            processed = sd_processor.process_images(p)
            
            out_frame = np.asarray(processed.images[0])
            
            # Color correction (could do this before SD if applied to input only)
            cc_window_start, cc_window_end  = self.compute_cc_target_window(frame_pos, cc_window_size, cc_window_rate)
            cc_apply_to_output = False #TODO - make this an option
            cc_include_initial_image = False #TODO - make this an option
            if (cc_window_end>0):
                cc_target_images = out_frame_history[cc_window_start:cc_window_end]
            else:
                cc_target_images = []
            if (cc_include_initial_image):
                cc_target_images.append(initial_input_image)

            cc_target_histogram = compute_cc_target(cc_target_images)
            if cc_target_histogram is None:
                logging.debug(f"Skipping color correction on frame {frame_pos} (target frames: {cc_window_start} to {cc_window_end})")
                out_frame_with_cc = out_frame
            else:
                logging.debug(f"Applying color correction on frame {frame_pos} (target frames: {cc_window_start} to {cc_window_end}) effective window size: {len(cc_target_images)})")
                out_frame_with_cc = apply_color_correction(out_frame, cc_target_histogram)
                if cc_apply_to_output:
                    out_frame = out_frame_with_cc

            # Save frame
            process2.stdin.write(
                out_frame
                .astype(np.uint8)
                .tobytes()
            )

            # Save frames for loopback
            out_frame_history.append(out_frame)
            frame_pos += 1

        process2.stdin.close()
        process1.wait()
        process2.wait()

        return [out_frame_history, "Here's some info mate. Where you gonna put it mate?"]


    def compute_cc_target_window(self, current_pos, window_size, window_rate):
        cc_window_end = round((current_pos)*window_rate)
        if window_size == -1:
            cc_window_start = 0
        else:
            cc_window_start = max(0, cc_window_end-window_size)
        return cc_window_start, cc_window_end


    def blend_frames(self, frames_to_blend, decay):
        if len(frames_to_blend) == 1:
            return frames_to_blend[0]
        return cv2.addWeighted(frames_to_blend[0], (1-decay), self.blend_frames(frames_to_blend[1:], decay), decay, 0)

#### Image conversion utils
def convert_from_cv2_to_image(img: np.ndarray) -> Image:
    return Image.fromarray(img)


def convert_from_image_to_cv2(img: Image) -> np.ndarray:
    return np.asarray(img)

def compute_cc_target(target_images):
    if target_images is None or len(target_images)==0:
        return None

    target_histogram = np.zeros(np.shape(target_images[0])).astype('float64')
    for img in target_images:
        target_histogram_component = cv2.cvtColor(img.copy(), cv2.COLOR_RGB2LAB).astype('float64')
        target_histogram += (target_histogram_component/len(target_images)).astype('float64')
                
    target_histogram=target_histogram.astype('uint8')
    
    return target_histogram

def apply_color_correction(image, target):
    logging.debug("Applying color correction.")
    corrected = cv2.cvtColor(exposure.match_histograms(
        cv2.cvtColor(
            image.copy(),
            cv2.COLOR_RGB2LAB
        ),
        target,
        channel_axis=2
    ), cv2.COLOR_LAB2RGB).astype("uint8")
    return corrected


#### Param script utils:
def load_param_script(param_script_string):
    param_script_raw = json.loads(param_script_string)['rendered_frames']
    param_script = dict()
    for event in param_script_raw:
        if event['frame'] in param_script:
            logging.debug(f"Duplicate frame {event['frame']} detected. Latest wins.")        
        param_script[event['frame']] = event

    last_frame=max(param_script.keys())
    logging.info(f"Script contains {len(param_script)} frames, last frame is {last_frame}")
    
    for f in range(0, last_frame+1):
        if not event['frame'] in param_script:
            logging.warning(f"Script should contain contiguous frame definitions, but is missing frame {f}.")

    return param_script

#### Math utils:
def clamp(minvalue, value, maxvalue):
    return max(minvalue, min(value, maxvalue))


#### Video utils:

def video_frames(video_file):
    num_frames = get_video_info(video_file)['nb_frames']
    return num_frames

def video_width(video_file):
    return get_video_info(video_file)['width']

def video_height(video_file):
    return get_video_info(video_file)['height']

def video_fps(video_file):
    return  int(get_video_info(video_file)['r_frame_rate'].split('/')[0])

def get_video_info(video_file):
    probe = ffmpeg.probe(video_file)
    video_info = next(s for s in probe['streams'] if s['codec_type'] == 'video')
    return video_info

def save_video(video_name, path = './', files=[], fps=10, smooth=True):
    video_name = path + video_name
    txt_name = video_name + '.txt'

    # save pics path in txt
    open(txt_name, 'w').write('\n'.join(["file '" + os.path.join(path, f) + "'" for f in files]))

    subprocess.call(' '.join([
        'ffmpeg/ffmpeg -y',
        f'-r {fps}',
        '-f concat -safe 0',
        f'-i "{txt_name}"',
        '-vcodec libx264',
        '-filter:v minterpolate' if smooth else '',   # smooth between images
        '-crf 10',
        '-pix_fmt yuv420p',
        f'"{video_name}"'
    ]))
    return video_name

# from https://github.com/eborboihuc/rotate_3d/blob/master/image_transformer.py
# License: https://github.com/eborboihuc/rotate_3d/blob/master/LICENSE.md
class ImageTransformer():
    """ Perspective transformation class for image
        with shape (height, width, #channels) """

    """ Wrapper of Rotating a Image """
    def rotate_along_axis(self, img, theta=0, phi=0, gamma=0, dx=0, dy=0, dz=0):

        self.image = img
        self.height = img.shape[0]
        self.width = img.shape[1]
        self.num_channels = img.shape[2]
        
        # Get radius of rotation along 3 axes
        rtheta, rphi, rgamma = self.get_rad(theta, phi, gamma)
        
        # Get ideal focal length on z axis
        # NOTE: Change this section to other axis if needed
        d = np.sqrt(self.height**2 + self.width**2)
        self.focal = d / (2 * np.sin(rgamma) if np.sin(rgamma) != 0 else 1)
        dz += self.focal

        # Get projection matrix
        mat = self.get_M(rtheta, rphi, rgamma, dx, dy, dz)
        
        return cv2.warpPerspective(self.image.copy(), mat, (self.width, self.height))


    """ Get Perspective Projection Matrix """
    def get_M(self, theta, phi, gamma, dx, dy, dz):
        
        w = self.width
        h = self.height
        f = self.focal

        # Projection 2D -> 3D matrix
        A1 = np.array([ [1, 0, -w/2],
                        [0, 1, -h/2],
                        [0, 0, 1],
                        [0, 0, 1]])
        
        # Rotation matrices around the X, Y, and Z axis
        RX = np.array([ [1, 0, 0, 0],
                        [0, np.cos(theta), -np.sin(theta), 0],
                        [0, np.sin(theta), np.cos(theta), 0],
                        [0, 0, 0, 1]])
        
        RY = np.array([ [np.cos(phi), 0, -np.sin(phi), 0],
                        [0, 1, 0, 0],
                        [np.sin(phi), 0, np.cos(phi), 0],
                        [0, 0, 0, 1]])
        
        RZ = np.array([ [np.cos(gamma), -np.sin(gamma), 0, 0],
                        [np.sin(gamma), np.cos(gamma), 0, 0],
                        [0, 0, 1, 0],
                        [0, 0, 0, 1]])

        # Composed rotation matrix with (RX, RY, RZ)
        R = np.dot(np.dot(RX, RY), RZ)

        # Translation matrix
        T = np.array([  [1, 0, 0, dx],
                        [0, 1, 0, dy],
                        [0, 0, 1, dz],
                        [0, 0, 0, 1]])

        # Projection 3D -> 2D matrix
        A2 = np.array([ [f, 0, w/2, 0],
                        [0, f, h/2, 0],
                        [0, 0, 1, 0]])

        # Final transformation matrix
        return np.dot(A2, np.dot(T, np.dot(R, A1)))

    def get_rad(self, theta, phi, gamma):
        return (self.deg_to_rad(theta),
                self.deg_to_rad(phi),
                self.deg_to_rad(gamma))

    def get_deg(self, rtheta, rphi, rgamma):
        return (self.rad_to_deg(rtheta),
                self.rad_to_deg(rphi),
                self.rad_to_deg(rgamma))

    def deg_to_rad(self, deg):
        return deg * pi / 180.0

    def rad_to_deg(self, rad):
        return rad * 180.0 / pi        