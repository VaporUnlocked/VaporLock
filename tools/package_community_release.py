"""Assemble local community artifacts, without publishing or contacting services."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sys
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_public_source import _create_retained_output, _output_identity


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def plain(path):
    path = path.absolute()
    for node in (path, *path.parents):
        if node.exists() and (node.is_symlink() or getattr(node.lstat(), 'st_file_attributes', 0) & 0x400):
            raise ValueError('Linked assembly path rejected')
    return path


def copied(source, target):
    plain(source)
    plain(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise ValueError('Assembly refuses an existing destination: '+target.name)
    expected = digest(source)
    with source.open('rb') as input_file, target.open('xb') as output:
        shutil.copyfileobj(input_file, output, 1024 * 1024)
    if digest(target) != expected:
        raise ValueError('Assembly copy mismatch')


def inventory(root):
    rows = []
    for folder, dirs, files in os.walk(root, followlinks=False):
        plain(Path(folder))
        for name in dirs:
            plain(Path(folder)/name)
        for name in files:
            path = plain(Path(folder)/name)
            relative = path.relative_to(root).as_posix()
            if relative in ('information/RELEASE_MANIFEST.json','information/RELEASE_CHECKSUMS.sha256'):
                continue
            parts=Path(relative).parts
            if parts[0].casefold() in ('models','profiles','server_runtime','license_service','discord_key_bot') or relative.startswith('app/controller-host/'):
                raise ValueError('Private or unsupported component in release inventory')
            rows.append({'path':relative,'bytes':path.stat().st_size,'sha256':digest(path)})
    return sorted(rows,key=lambda row:row['path'])


def archive(root, output, rows, *, cuda):
    selected = [row for row in rows if row['path'].startswith('runtime/cloud/cuda/') == cuda]
    if not cuda:
        for name in ('RELEASE_MANIFEST.json','RELEASE_CHECKSUMS.sha256'):
            path=root/'information'/name
            selected.append({'path':'information/'+name,'bytes':path.stat().st_size,'sha256':digest(path)})
    if output.exists():
        raise ValueError('Refusing existing release archive')
    with _create_retained_output(output) as file:
        with zipfile.ZipFile(file,'w',zipfile.ZIP_DEFLATED,compresslevel=5) as package:
            for row in selected:
                path=root/row['path']
                if digest(path)!=row['sha256']:
                    raise ValueError('Release input changed')
                package.write(path,'VaporLock/'+row['path'])
        file.flush()
        os.fsync(file.fileno())
        frozen=_output_identity(output,file,links=1)
        file.seek(0)
        with zipfile.ZipFile(file) as package:
            if len(package.infolist())!=len(selected):raise ValueError('Release member count mismatch')
            for row in selected:
                checksum=hashlib.sha256();total=0
                with package.open('VaporLock/'+row['path']) as member:
                    for chunk in iter(lambda:member.read(1024*1024),b''):
                        checksum.update(chunk);total+=len(chunk)
                if total!=row['bytes'] or checksum.hexdigest()!=row['sha256']:
                    raise ValueError('Release archive member readback mismatch')
        file.seek(0)
        sha=hashlib.file_digest(file,'sha256').hexdigest()
        _output_identity(output,file,links=1,frozen=frozen)
    if output.stat().st_size>=2*1024**3:
        raise ValueError('Release asset exceeds GitHub per-file limit')
    return {'name':output.name,'bytes':output.stat().st_size,'sha256':sha,'files':len(selected),'all_members_readback':True}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package',type=Path,required=True)
    parser.add_argument('--native',type=Path,required=True)
    parser.add_argument('--suite',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--assemble-only',action='store_true')
    parser.add_argument('--archive-only',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    package=plain(args.package)
    if not package.is_relative_to(root/'build'):
        raise ValueError('Assembly requires a new build package')
    version=(root/'product_version.txt').read_text().strip()
    if not args.archive_only:
        copied(args.native/'VaporLockLauncher.exe',package/'VaporLock.exe')
        for directory, names in (
            ('',('VaporLock.exe','onnxruntime.dll','onnxruntime_providers_shared.dll')),
            ('dml',('VaporLockDml.exe','onnxruntime.dll','onnxruntime_providers_shared.dll')),
            ('trt',('VaporLockTensorRt.exe','onnxruntime.dll','onnxruntime_providers_shared.dll','tensorrt_rtx_1_6.dll','tensorrt_onnxparser_rtx_1_6.dll'))):
            for name in names:
                copied(args.native/directory/name,package/'engine'/directory/name)
        # DirectML is an independently licensed vendor runtime already present
        # in the verified Cloud DML cohort. Do not copy an old product engine.
        copied(package/'runtime/cloud/dml/onnxruntime/capi/DirectML.dll',package/'engine/dml/DirectML.dll')
        qt=package/'app/_internal'
        for directory in ('','dml','trt'):
            for name in ('vcruntime140.dll','vcruntime140_1.dll','msvcp140.dll','msvcp140_1.dll','msvcp140_2.dll','msvcp140_codecvt_ids.dll','concrt140.dll'):
                if (qt/name).is_file():copied(qt/name,package/'engine'/directory/name)
        copied(args.suite,package/'testing-suite/Official Aim Testing Suite.exe')
        for name in ('LICENSE','PRIVACY.md','README.md','THIRD_PARTY_NOTICES.md'):
            copied(root/name,package/'information'/name)
        for name in ('LICENSE-GPL-3.0.txt','LICENSE-LGPL-3.0.txt'):
            copied(root/'third_party/qt'/name,package/'information/third-party/qt'/name)
        copied(root/'docs/EULA.md',package/'information/EULA.md')
        for directory, dirs, files in os.walk(root/'guides',followlinks=False):
            plain(Path(directory))
            for name in dirs:plain(Path(directory)/name)
            for name in files:
                path=plain(Path(directory)/name)
                if path.suffix.casefold() in ('.html','.css','.png','.svg'):
                    copied(path,package/'guides'/path.relative_to(root/'guides'))
        copied(root/'VaporLock-Settings-Guide.html',package/'VaporLock-Settings-Guide.html')
        (package/'README.txt').write_text('VaporLock '+version+' Community\n\nExtract the core ZIP, then open VaporLock.exe. No account or key is required.\nFor .cloud CUDA, extract the optional CUDA ZIP into the same parent so its VaporLock folder merges with this one.\nThe Windows controller-output component is not bundled because its embedded dependency redistribution permissions are unresolved.\nNo installer or automatic updates are included. See information/README.md, LICENSE and THIRD_PARTY_NOTICES.md.\n',encoding='utf-8')
    rows=inventory(package)
    metadata={'schema_version':1,'version':version,'edition':'community','unsigned':True,
              'purpose':'local file integrity and optional reproducible dependency reuse; no activation or update gate','files':rows}
    information=package/'information';information.mkdir(exist_ok=True)
    (information/'RELEASE_MANIFEST.json').write_text(json.dumps(metadata,indent=2)+'\n',encoding='utf-8')
    (information/'RELEASE_CHECKSUMS.sha256').write_text(''.join(row['sha256']+'  '+row['path']+'\n' for row in rows),encoding='utf-8')
    if args.assemble_only:
        print(json.dumps({'assembled':True,'files':len(rows),'package':str(package)}))
        return
    output=plain(args.output);output.mkdir(parents=True,exist_ok=True)
    results=[archive(package,output/('VaporLock-'+version+'-Community-Windows-x64.zip'),rows,cuda=False),
             archive(package,output/('VaporLock-'+version+'-Cloud-CUDA-Addon.zip'),rows,cuda=True)]
    (output/'release-artifacts.json').write_text(json.dumps({'version':version,'assets':results},indent=2)+'\n',encoding='utf-8')
    (output/'SHA256SUMS.txt').write_text(''.join(r['sha256']+'  '+r['name']+'\n' for r in results),encoding='utf-8')
    print(json.dumps({'assets':results}))


if __name__=='__main__':main()
