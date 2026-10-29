"""Bounded valid-ROI warmup; never returns an observation or detection."""
def warmup(runtime):
    side=runtime.config.input_size
    torch=runtime._torch
    if torch is None:
        import numpy as np
        features,_,_=runtime.network.detect(np.zeros((1,3,side,side),dtype=np.float32))
        for count in getattr(runtime.network,'warmup_roi_counts',(1,)):
            runtime.network.wrap(features,np.tile(np.asarray([[.25,.25,.75,.75]],dtype=np.float32),(count,1)),
                                 np.zeros(count,dtype=np.int64))
    else:
        with torch.inference_mode():
            features,_,_=runtime.network.detect(torch.zeros((1,3,side,side),device=runtime.device))
            runtime.network.wrap(features,torch.tensor([[.25,.25,.75,.75]],device=runtime.device),
                                 torch.zeros(1,dtype=torch.int64,device=runtime.device))
        runtime._synchronize()
    release=getattr(runtime.network,'release_observation',None)
    if release is not None:release()
