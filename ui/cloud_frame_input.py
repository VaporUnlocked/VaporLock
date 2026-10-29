"""Validated private BGRX frames to fresh Cloud input; no retained frame views."""
from cloud_contract import CloudInputError,MAX_FRAME_PIXELS


def prepare_bgrx(width,height,pixels,size):
    if (type(width) is not int or type(height) is not int or not 1<=width<=4096 or not 1<=height<=4096
            or width*height>MAX_FRAME_PIXELS or type(pixels) not in (bytes,bytearray)
            or len(pixels)!=width*height*4 or type(size) is not int or not 32<=size<=2048):
        raise CloudInputError('Invalid .cloud packed frame or model input size.')
    if width==height==size:
        import numpy as np
        source=np.frombuffer(pixels,dtype=np.uint8).reshape(height,width,4)
        array=np.empty((3,height,width),dtype=np.float32)
        # Reversed B/G/R view excludes X without an intermediate RGB image.
        # Only the newly allocated FP32 result leaves this function.
        np.divide(source[:,:,2::-1].transpose(2,0,1),np.float32(255),out=array,dtype=np.float32)
        return array,dict(scale=1.0,scale_x=1.0,scale_y=1.0,pad_x=0,pad_y=0,
            original_width=width,original_height=height,size=size)
    from PIL import Image
    from cloud_sdk.geometry import letterbox
    image=Image.frombytes('RGB',(width,height),bytes(pixels),'raw','BGRX')
    return letterbox(image,size)
