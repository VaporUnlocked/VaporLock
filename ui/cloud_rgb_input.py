"""Pure exact-size RGB input preparation shared by code-owned Cloud paths."""


def exact_rgb_input(image,size):
    """Return a fresh bit-exact input, or None for original SDK handling.

    No model, transport, thread, authorization or runtime is selected here.
    Callers own scoping/provenance. Metadata and generic image cases stay on
    the SDK path; never normalize away orientation or other input metadata.
    """
    from PIL import Image
    if (type(image) is not Image.Image or image.mode!='RGB' or image.info or image.getexif()
            or type(size) is not int or not 32<=size<=2048 or image.size!=(size,size)):
        return None
    import numpy as np
    pixels=np.asarray(image)
    array=np.empty((3,size,size),dtype=np.float32)
    np.divide(pixels.transpose(2,0,1),np.float32(255.0),out=array,dtype=np.float32)
    return array,dict(scale=1.0,scale_x=1.0,scale_y=1.0,pad_x=0,pad_y=0,
                      original_width=size,original_height=size,size=size)
