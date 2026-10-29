"""Candidate dedicated Cloud annotation child; never writes project state."""
import sys


def main():
    if sys.argv[1:]!=['--gated'] or sys.stdin.buffer.readline(32)!=b'VLCLOUD-GO-1\n':
        return 2
    from pathlib import Path
    import os
    sys.dont_write_bytecode=True
    if not getattr(sys,'frozen',False):sys.path.insert(0,str(Path(__file__).resolve().parent))
    from cloud_worker import read_request,emit
    from cloud_annotation_protocol import command,event,PROTOCOL
    from cloud_contract import CloudInputError,bundled_dependency_error
    assistant=None;terminal=False
    def publish(payload):
        event(payload);emit(payload)
    try:
        initial=read_request()
        if (set(initial)!={'operation','device'} or initial['operation']!='annotation'
                or initial['device'] not in ('cpu','cuda')):
            raise CloudInputError('Invalid annotation worker startup.')
        from cloud_access import edition

        # Codec-only common dependencies first; no Torch import during decode.
        from cloud_dependencies import activate_common
        activate_common()
        from cloud_annotation_assist import MaskAssistant
        assistant=MaskAssistant(initial['device'])
        publish(dict(type='annotation_ready',protocol=PROTOCOL,edition=edition(),pid=os.getpid(),device=initial['device']))
        while True:
            request=read_request();kind=command(request)
            if kind=='close':
                assistant.close();assistant=None
                publish(dict(type='annotation_closed'));terminal=True;return 0
            try:
                if kind=='decode':
                    from cloud_annotation_decode import stage
                    result=stage(request)
                else:result=assistant.propose(request)
                publish(result)
            except Exception as exc:
                message=str(exc) if isinstance(exc,CloudInputError) else 'Image processing failed ('+type(exc).__name__+').'
                message=bundled_dependency_error(exc) or message
                if kind=='mask':publish(dict(type='mask_failed',identity=request['identity'],message=message[:1024]))
                else:publish(dict(type='decode_failed',request=request['request'],capability=request['capability'],message=message[:1024]))
    except Exception as exc:
        if not terminal:
            message=str(exc) if isinstance(exc,CloudInputError) else '.cloud annotation stopped ('+type(exc).__name__+').'
            message=bundled_dependency_error(exc) or message
            publish(dict(type='error',message=message[:1024]));terminal=True
        return 1
    finally:
        if assistant is not None:assistant.close()


if __name__=='__main__':raise SystemExit(main())
