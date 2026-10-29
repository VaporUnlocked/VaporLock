"""Fixed-origin Ultralytics annotation preview and explicitly confirmed writes.

No model download, paid inference or class mutation. Cloud originals are deleted
only after their individually verified replacements exist. Production
HTTP rejects redirects and pins a validated public IP while retaining TLS SNI.
"""
from contextlib import contextmanager
from dataclasses import asdict
import hashlib
import http.client
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import secrets
import time
import threading
from urllib.parse import urlencode, urlsplit
import zipfile

from annotation_output import (MAX_IMAGE_BYTES, MAX_OUTPUT_BYTES, MAX_IMAGES,
    decode_image, digest, held_directory, new_output, parse_labels, plain,
    read_bytes, write_new)
from annotation_variations import VariationOptions
from annotation_progress import Progress

API_HOST = 'platform.ultralytics.com'
STORAGE_HOSTS = frozenset(('storage.googleapis.com',))  # documented exact host
MEDIA_HOSTS = frozenset(('storage.googleapis.com','cdn.ul.run'))
SLUG = re.compile(r'[a-z0-9]+(?:-[a-z0-9]+)*\Z')
ID = re.compile(r'[0-9a-fA-F]{24}\Z')
HASH = re.compile(r'[0-9a-f]{32}\Z')  # Platform CAS: XXH3-128, not MD5
MAX_JSON = 4 * 1024 * 1024
MAX_ARCHIVE = 2 * 1024**3
# In-place ingest replacement remains unsupported; verified upload/delete is separate.
PIXEL_REPLACEMENT_VERIFIED = False


class PlatformError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__('Ultralytics task stopped: ' + code)


class MutationUncertain(PlatformError):
    def __init__(self):
        super().__init__('OUTCOME_UNCERTAIN')


def target_path(owner, dataset):
    if (type(owner) is not str or not 4 <= len(owner) <= 32 or not SLUG.fullmatch(owner)
            or type(dataset) is not str or not 1 <= len(dataset) <= 128 or not SLUG.fullmatch(dataset)):
        raise PlatformError('INVALID_TARGET')
    return '/api/datasets/' + owner + '/' + dataset


def identifier(value):
    if type(value) is not str or ID.fullmatch(value) is None:
        raise PlatformError('INVALID_IMAGE_ID')
    return value


def storage_url(url, *, media=False):
    if type(url) is not str or not 1 <= len(url) <= 8192 or any(c.isspace() or c == '\\' for c in url):
        raise PlatformError('UNSUPPORTED_STORAGE_URL')
    parsed = urlsplit(url)
    try:
        port = parsed.port
    except ValueError:
        raise PlatformError('UNSUPPORTED_STORAGE_URL') from None
    if (parsed.scheme != 'https' or parsed.hostname not in (MEDIA_HOSTS if media else STORAGE_HOSTS)
            or port not in (None, 443) or parsed.username is not None or parsed.password is not None
            or parsed.fragment or not parsed.path.startswith('/')):
        raise PlatformError('UNSUPPORTED_STORAGE_URL')
    return parsed


def public_addresses(host):
    addresses = []
    for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
        address = item[4][0]
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_multicast or ip.is_reserved:
            raise PlatformError('UNSAFE_NETWORK_ADDRESS')
        if address not in addresses:
            addresses.append(address)
    if not addresses:
        raise PlatformError('NETWORK_UNAVAILABLE')
    return addresses


class _PinnedTLS(http.client.HTTPSConnection):
    def connect(self):
        addresses = public_addresses(self.host)
        remaining=getattr(self,'_request_deadline',time.monotonic()+20)-time.monotonic()
        if remaining<=0:raise TimeoutError('Request deadline exceeded')
        self.timeout=min(self.timeout,remaining)
        raw = socket.create_connection((addresses[0], 443), timeout=self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class _HTTP:
    def __init__(self):
        self._connections={}
        self._context=ssl.create_default_context()

    def close(self):
        for connection,*_rest in self._connections.values():connection.close()
        self._connections.clear()

    def request(self, host, method, path, headers, body=None, maximum=MAX_JSON, deadline=None):
        if host not in MEDIA_HOSTS|STORAGE_HOSTS|{API_HOST} or (host!=API_HOST and any(k.lower()=='authorization' for k in headers)):
            raise PlatformError('INVALID_HOST')
        previous=self._connections.pop(host,None)
        now=time.monotonic()
        if previous is not None and (now-previous[1]>=30 or previous[2]>=32 or previous[3]>=64*1024**2):
            previous[0].close();previous=None
        connection,started,requests,received=previous or (_PinnedTLS(host,timeout=20,context=self._context),now,0,0)
        if deadline is not None and deadline<=now:
            connection.close();raise TimeoutError('Request deadline exceeded')
        connection._request_deadline=deadline if deadline is not None else now+20
        connection.timeout=max(.001,min(20,deadline-now)) if deadline is not None else 20
        if connection.sock is not None:connection.sock.settimeout(connection.timeout)
        def expire():
            sock=connection.sock
            if sock is not None:
                try:sock.shutdown(socket.SHUT_RDWR)
                except OSError:pass
        watchdog=None
        if deadline is not None:
            watchdog=threading.Timer(deadline-now,expire);watchdog.daemon=True;watchdog.start()
        reusable=False
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise PlatformError('REDIRECT_REJECTED')
            length = response.getheader('Content-Length')
            if length is not None and (not length.isdigit() or int(length) > maximum):
                raise PlatformError('RESPONSE_TOO_LARGE')
            data = response.read(maximum + 1)
            if len(data) > maximum:
                raise PlatformError('RESPONSE_TOO_LARGE')
            if length is not None and len(data)!=int(length):raise PlatformError('INCOMPLETE_RESPONSE')
            if deadline is not None and time.monotonic()>=deadline:raise TimeoutError('Request deadline exceeded')
            reusable=response.isclosed() and not response.will_close and connection.sock is not None
            return response.status, dict(response.getheaders()), data
        finally:
            if watchdog is not None:
                watchdog.cancel();watchdog.join(timeout=.1)
                if watchdog.is_alive() or time.monotonic()>=deadline:reusable=False
            if reusable:self._connections[host]=(connection,started,requests+1,received+len(data))
            else:connection.close()


class PlatformClient:
    def __init__(self, key, *, transport=None, wait=time.sleep, check_cancel=lambda:None):
        if type(key) is not str or re.fullmatch(r'ul_[0-9a-fA-F]{40}', key) is None:
            raise PlatformError('INVALID_KEY')
        self._key, self._http, self._wait = key, transport or _HTTP(), wait
        self._last_request = 0.0
        self._request_times={}
        self._progress=lambda *_a,**_kw:None
        self._check_cancel=check_cancel

    def _pause(self,seconds,*,deadline=None):
        until=time.monotonic()+seconds
        self._check_cancel()
        while time.monotonic()<until:
            if deadline is not None and time.monotonic()>=deadline:raise PlatformError('REPLACEMENT_IMPORT_PENDING')
            self._wait(max(0,min(.05,until-time.monotonic(),deadline-time.monotonic() if deadline is not None else .05)))
            if seconds>=.1:self._progress(waiting=True)
            self._check_cancel()

    def close(self):
        self._key = ''
        close=getattr(self._http,'close',None)
        if close is not None:close()

    def _api(self, method, route, payload=None,*,deadline=None):
        if not route.startswith('/api/') or len(route) > 2048:
            raise PlatformError('INVALID_ROUTE')
        body = json.dumps(payload, allow_nan=False).encode('utf-8') if payload is not None else None
        headers = {'Authorization': 'Bearer ' + self._key, 'Content-Type': 'application/json',
                   'User-Agent': 'VaporLock-AnnotationSuite/1'}
        for attempt in range(3 if method == 'GET' else 1):
            self._check_cancel()
            if deadline is not None and time.monotonic()>=deadline:raise PlatformError('REPLACEMENT_IMPORT_PENDING')
            category='upload' if route in ('/api/upload/signed-url','/api/upload/complete') or route.endswith('/ingest') else 'default'
            interval=6.1 if category=='upload' else .62
            budget={'deadline':deadline} if deadline is not None else {}
            self._pause(max(0, interval - (time.monotonic() - self._request_times.get(category,0))),**budget)
            self._last_request = time.monotonic()
            self._request_times[category]=self._last_request
            try:
                status, response_headers, data = self._http.request(API_HOST, method, route, headers, body,**budget)
            except (OSError, http.client.HTTPException, PlatformError):
                if method != 'GET':
                    raise MutationUncertain() from None
                if attempt == 2:
                    raise PlatformError('NETWORK_UNAVAILABLE') from None
                continue
            if status == 429 and method == 'GET' and attempt < 2:
                try:
                    delay = float(response_headers.get('Retry-After', '2'))
                except (TypeError, ValueError):
                    delay = 2
                if not math.isfinite(delay) or not 0 <= delay <= 60:
                    raise PlatformError('RATE_LIMIT')
                self._pause(delay,**budget)
                continue
            if deadline is not None and time.monotonic()>=deadline:raise PlatformError('REPLACEMENT_IMPORT_PENDING')
            if status not in (200, 201, 202):
                if status == 404 and method == 'GET' and re.fullmatch(r'/api/images/[0-9a-fA-F]{24}',route):
                    raise PlatformError('IMAGE_NOT_FOUND')
                code = {401:'AUTHENTICATION_FAILED',403:'PERMISSION_DENIED',429:'RATE_LIMIT',409:'TARGET_BUSY'}.get(status,'PLATFORM_REQUEST_FAILED')
                if method != 'GET' and status >= 500:
                    raise MutationUncertain()
                raise PlatformError(code)
            try:
                result = json.loads(data)
                if type(result) is not dict:
                    raise ValueError()
            except (ValueError, UnicodeError):
                if method != 'GET':
                    raise MutationUncertain() from None
                raise PlatformError('INVALID_RESPONSE') from None
            return result
        raise PlatformError('RATE_LIMIT')

    def dataset(self, owner, dataset):
        result = self._api('GET', target_path(owner, dataset)).get('dataset')
        if (type(result) is not dict or result.get('owner') != owner or result.get('dataset') != dataset
                or result.get('task') not in ('detect','segment') or result.get('status') != 'ready'):
            raise PlatformError('UNSUPPORTED_OR_BUSY_DATASET')
        identifier(result.get('id'))
        names = result.get('classNames')
        if (type(names) is not list or not 1 <= len(names) <= 25000 or
                any(type(n) is not str or not n.strip() or len(n)>100 for n in names)
                or len({n.strip().casefold() for n in names}) != len(names)):
            raise PlatformError('INVALID_CLASS_MAP')
        return {'id':result['id'],'owner':owner,'dataset':dataset,'task':result['task'],'classNames':names}

    def images(self, target, limit):
        if type(limit) is not int or not 1 <= limit <= MAX_IMAGES:
            raise PlatformError('INVALID_IMAGE_LIMIT')
        cursor = None; seen_cursors = set(); seen = set(); pages = 0
        while len(seen) < limit:
            pages += 1
            if pages > MAX_IMAGES:
                raise PlatformError('PAGINATION_LIMIT')
            query = {'limit':min(100,limit-len(seen)),'hasLabel':'false','includeImageUrls':'true',
                     'includeLabels':'false','includeThumbnails':'false','includeTotal':'false','sort':'oldest'}
            if cursor is not None:
                query['cursor'] = cursor
            data = self._api('GET', target_path(target['owner'],target['dataset'])+'/images?'+urlencode(query))
            items = data.get('images')
            if type(items) is not list or len(items)>100 or type(data.get('hasMore')) is not bool:
                raise PlatformError('INVALID_RESPONSE')
            for item in items:
                if type(item) is not dict:
                    raise PlatformError('INVALID_RESPONSE')
                image_id = identifier(item.get('id'))
                if image_id in seen:
                    continue
                seen.add(image_id)
                if item.get('labelCount') != 0 or item.get('labelsTruncated'):
                    continue
                yield item
                if len(seen) >= limit:
                    return
            if not data['hasMore']:
                return
            cursor = identifier(data.get('nextCursor'))
            if cursor in seen_cursors:
                raise PlatformError('REPEATED_CURSOR')
            seen_cursors.add(cursor)

    def full_image(self, target, image_id):
        data=self._image_detail(target,image_id)
        return data['properties'],data['labels']

    def _image_detail(self, target, image_id):
        data = self._api('GET','/api/images/'+identifier(image_id))
        props = data.get('properties')
        if (type(props) is not dict or props.get('id') != image_id or props.get('datasetId') != target['id']
                or props.get('split') not in ('train','val','test') or props.get('error')
                or not HASH.fullmatch(str(props.get('hash',''))) or data.get('classNames') != target['classNames']
                or data.get('labelsTruncated') or type(data.get('labels')) is not list
                or type(props.get('annotationCount')) is not int or props['annotationCount'] != len(data['labels'])):
            raise PlatformError('IMAGE_STATE_CHANGED')
        return data

    def download(self, url):
        parsed = storage_url(url,media=True)
        path = parsed.path + ('?' + parsed.query if parsed.query else '')
        try:
            status, _, data = self._http.request(parsed.hostname,'GET',path,
                {'User-Agent':'VaporLock-AnnotationSuite/1'},maximum=MAX_IMAGE_BYTES)
        except (OSError,http.client.HTTPException):
            raise PlatformError('MEDIA_DOWNLOAD_FAILED') from None
        if status != 200:
            raise PlatformError('MEDIA_DOWNLOAD_FAILED')
        return data

    def set_labels(self, image_id, labels, *, expected_target=None, expected_image=None):
        response = self._api('PATCH','/api/images/'+identifier(image_id),{'labels':labels})
        if response.get('success') is not True or response.get('labelCount') != len(labels) or type(response.get('labels')) is not list:
            raise MutationUncertain()
        # The PATCH response can differ from persisted coordinates. Use fresh
        # stored labels for subsequent image effects, never assume echo==storage.
        try:
            readback=self._api('GET','/api/images/'+identifier(image_id))
            props=readback.get('properties');stored=readback.get('labels')
            if (type(props) is not dict or props.get('id')!=image_id or type(stored) is not list
                    or readback.get('labelsTruncated') or len(stored)!=len(labels)
                    or props.get('annotationCount')!=len(stored)):
                raise ValueError('Incomplete stored label readback.')
            if expected_target is not None and (props.get('datasetId')!=expected_target['id']
                    or readback.get('classNames')!=expected_target['classNames']):
                raise ValueError('Stored target changed.')
            if expected_target is not None:
                labels_text(stored,len(expected_target['classNames']),expected_target['task'])
            if expected_image is not None and any(props.get(k)!=expected_image[k] for k in ('hash','width','height','split')):
                raise ValueError('Stored image changed.')
            if sorted(item.get('classId') for item in stored)!=sorted(item['classId'] for item in labels):
                raise ValueError('Stored classes changed.')
            if expected_image is not None and not geometry_corresponds(labels,stored,expected_image['width'],expected_image['height']):
                raise ValueError('Stored geometry does not correspond to the submitted annotations.')
            return stored
        except (PlatformError,ValueError,TypeError,KeyError,AttributeError):
            raise MutationUncertain() from None

    def image_snapshot(self, target, image_id):
        detail=self._image_detail(target,image_id)
        props,labels=detail['properties'],detail['labels']
        metadata=detail.get('metadata',{})
        if (type(metadata) is not dict or len(json.dumps(metadata,allow_nan=False))>500000
                or any(type(k) is not str or len(k)>128 for k in metadata)):
            raise PlatformError('INVALID_IMAGE_METADATA')
        return {'properties':props,'labels':labels,'metadata':metadata}

    def delete_image(self, image_id):
        image_id=identifier(image_id)
        response=self._api('DELETE','/api/images/'+image_id)
        if (response.get('success') is not True or response.get('deletedImageId')!=image_id
                or response.get('deletedCount')!=1):
            raise MutationUncertain()
        try:
            self._api('GET','/api/images/'+image_id)
        except Exception as error:
            if isinstance(error,PlatformError) and error.code=='IMAGE_NOT_FOUND':return
            raise MutationUncertain() from None
        raise MutationUncertain()

    def wait_ingest(self,target,job_id,*,seconds=1800):
        deadline=time.monotonic()+min(max(seconds,1),1800)
        self._progress('ingest',waiting=True,force=True)
        while time.monotonic()<deadline:
            self._check_cancel()
            data=self._api('GET',target_path(target['owner'],target['dataset']),deadline=deadline).get('dataset',{})
            if time.monotonic()>=deadline:raise PlatformError('REPLACEMENT_IMPORT_PENDING')
            if data.get('id')!=target['id'] or data.get('classNames')!=target['classNames'] or data.get('task')!=target['task']:
                raise PlatformError('TARGET_CHANGED')
            if data.get('status')=='failed':raise PlatformError('REPLACEMENT_IMPORT_FAILED')
            if data.get('lastIngestJobId')==job_id and data.get('status')=='ready':
                self._progress('ingest',current=1,total=1,force=True);return
            self._progress('ingest',waiting=True)
            self._pause(2,deadline=deadline)
        raise PlatformError('REPLACEMENT_IMPORT_PENDING')

    def replacement_images(self,target,prefix,limit):
        cursor=None;seen=set();result=[]
        while True:
            query={'limit':min(1000,limit+1),'search':prefix,'sort':'oldest','includeLabels':'false',
                   'includeImageUrls':'true','includeThumbnails':'false','includeTotal':'false'}
            if cursor:query['cursor']=cursor
            data=self._api('GET',target_path(target['owner'],target['dataset'])+'/images?'+urlencode(query))
            if type(data.get('images')) is not list or type(data.get('hasMore')) is not bool:
                raise PlatformError('INVALID_RESPONSE')
            result.extend(data['images'])
            if len(result)>limit:raise PlatformError('REPLACEMENT_MEMBERSHIP_CHANGED')
            if not data['hasMore']:return result
            cursor=identifier(data.get('nextCursor'))
            if cursor in seen:raise PlatformError('REPEATED_CURSOR')
            seen.add(cursor)

    def ingest_variants(self, target, archive, *, expected_sha256=None, image_metadata=None, record=lambda *_a,**_kw:None):
        archive = plain(archive)
        size = archive.stat().st_size
        if not 0 < size <= MAX_ARCHIVE:
            raise PlatformError('VARIANT_ARCHIVE_TOO_LARGE')
        self._progress('upload',bytes_current=0,bytes_total=size,force=True)
        record('signed_url_pending',assetId=target['id'],bytes=size)
        signed = self._api('POST','/api/upload/signed-url',{'assetId':target['id'],
            'assetType':'datasets','contentType':'application/zip','filename':archive.name,'totalBytes':size})
        session_id = signed.get('sessionId'); headers = signed.get('headers')
        if type(session_id) is not str or not 1 <= len(session_id) <= 128 or type(headers) is not dict:
            raise PlatformError('INVALID_UPLOAD_SESSION')
        if set(headers) != {'x-goog-if-generation-match'} or headers['x-goog-if-generation-match'] != '0':
            raise PlatformError('UNSAFE_UPLOAD_HEADERS')
        record('upload_session_ready',sessionId=session_id,assetId=target['id'])
        parsed = storage_url(signed.get('uploadUrl'))
        request_headers = {'Content-Type':'application/zip','Content-Length':str(size),**headers}
        try:
            from dataset_transaction import HeldFile
            with held_directory(archive.parent),HeldFile(archive) as body:
                info=body.info()
                if ((info.size_high<<32)|info.size_low)!=size:raise PlatformError('VARIATIONS_CHANGED')
                actual_hash=hashlib.sha256()
                for block in body.blocks():
                    self._check_cancel()
                    actual_hash.update(block)
                if expected_sha256 is not None and actual_hash.hexdigest()!=expected_sha256:
                    raise PlatformError('VARIATIONS_CHANGED')
                self._check_cancel()
                record('storage_put_pending',sessionId=session_id)
                # A stop before dispatch is known; keep it outside the handler
                # that marks failures after starting a PUT as uncertain.
                self._check_cancel()
                try:
                    self._progress('upload',bytes_current=0,bytes_total=size,force=True)
                    def sending_blocks():
                        sent=0
                        for block in body.blocks():
                            self._check_cancel()
                            yield block
                            sent+=len(block)
                            self._progress('upload',bytes_current=sent,bytes_total=size,force=sent==size)
                    status, _, _ = self._http.request(parsed.hostname,'PUT',parsed.path+'?'+parsed.query,
                        request_headers,sending_blocks(),maximum=MAX_JSON)
                except (OSError,http.client.HTTPException,PlatformError):
                    raise MutationUncertain() from None
            if status not in (200,201):
                raise MutationUncertain()
        except (OSError,http.client.HTTPException):
            raise MutationUncertain() from None
        record('storage_put_confirmed',sessionId=session_id)
        record('upload_complete_pending',sessionId=session_id)
        self._api('POST','/api/upload/complete',{'sessionId':session_id})
        record('upload_complete_confirmed',sessionId=session_id)
        if self.dataset(target['owner'],target['dataset'])!=target:raise PlatformError('TARGET_CHANGED')
        record('ingest_pending',sessionId=session_id,conflictPolicy='skip')
        payload={'sessionId':session_id,'conflictPolicy':'skip','classMapping':{n:i for i,n in enumerate(target['classNames'])}}
        if image_metadata is not None:
            if type(image_metadata) is not dict or len(json.dumps(image_metadata,allow_nan=False))>500000:
                raise PlatformError('INVALID_IMAGE_METADATA')
            payload['imageMetadata']=image_metadata
        queued = self._api('POST',target_path(target['owner'],target['dataset'])+'/ingest',payload)
        if queued.get('status') != 'queued' or type(queued.get('jobId')) is not str:
            raise MutationUncertain()
        return queued['jobId']


def geometry_corresponds(requested,stored,width,height):
    """Conservative correspondence check, not a universal codec guarantee.

    Stored coordinates are authoritative. Reject a different shape, more than
    half a source pixel of displacement, or more than 1% box-area disagreement.
    A rejection after PATCH remains uncertain; it never triggers resubmission.
    """
    if type(width) is not int or type(height) is not int or min(width,height)<1 or len(requested)!=len(stored):return False
    def extent(label):
        if 'segments' in label:
            coordinates=label['segments']
            return min(coordinates[::2]),min(coordinates[1::2]),max(coordinates[::2]),max(coordinates[1::2])
        cx,cy,w,h=label['bbox'];return cx-w/2,cy-h/2,cx+w/2,cy+h/2
    try:
        for before,after in zip(requested,stored):
            shape='segments' if 'segments' in before else 'bbox'
            if before['classId']!=after['classId'] or shape not in after:return False
            a=before[shape];b=after[shape]
            if len(a)!=len(b) or any(type(v) not in (int,float) or not math.isfinite(v) for v in b):return False
            for index,(left,right) in enumerate(zip(a,b)):
                if abs(left-right)*(width if index%2==0 else height)>.5:return False
            x1,y1,x2,y2=extent(before);u1,v1,u2,v2=extent(after)
            intersection=max(0,min(x2,u2)-max(x1,u1))*max(0,min(y2,v2)-max(y1,v1))
            union=(x2-x1)*(y2-y1)+(u2-u1)*(v2-v1)-intersection
            if union<=0 or intersection/union<.99:return False
        return True
    except (KeyError,TypeError,ValueError):return False


def _source_hash(data, expected):
    if type(expected) is not str or HASH.fullmatch(expected) is None:
        raise PlatformError('UNSUPPORTED_IMAGE_HASH')
    import xxhash
    actual = xxhash.xxh3_128_hexdigest(data, seed=0)
    if actual != expected:
        raise PlatformError('IMAGE_CONTENT_CHANGED')


def preview(args, key, annotate, *, client_factory=PlatformClient, emit=lambda *_a,**_kw:None):
    progress=Progress(emit);progress('scan',force=True)
    client = client_factory(key)
    if isinstance(client,PlatformClient):
        client._check_cancel=getattr(args,'check_cancel',lambda:None);client._progress=progress
    try:
        target = client.dataset(args.owner,args.cloud_dataset)
        root = new_output(Path(args.dataset),'vaporlock-ultralytics-preview')
        write_new(root/'classes.txt', ('\n'.join(target['classNames'])+'\n').encode())
        records = []; total = 0
        for item in client.images(target,args.limit):
            progress('download',current=len(records),total=args.limit)
            props, labels = client.full_image(target,item['id'])
            if labels:
                continue
            data = client.download(item.get('imageUrl'))
            _source_hash(data,props['hash']); image = decode_image(data)
            if image.size != (props.get('width'),props.get('height')):
                raise PlatformError('IMAGE_DIMENSIONS_CHANGED')
            extension = str(props.get('extension','')).lower().lstrip('.')
            if extension not in ('jpg','jpeg','png','bmp','webp'):
                raise PlatformError('UNSUPPORTED_MEDIA')
            total += len(data)
            if total > MAX_OUTPUT_BYTES:
                raise PlatformError('PREVIEW_TOO_LARGE')
            name = item['id']+'.'+extension
            write_new(root/'images'/name,data)
            records.append({'id':item['id'],'image':'images/'+name,'sourceSHA256':digest(data),
                'hash':props['hash'],'split':props['split'],'width':image.width,'height':image.height,
                'updatedAt':props.get('updatedAt')})
            emit('cloud_progress',stage='download',current=len(records),total=args.limit)
        if not records:
            raise PlatformError('NO_UNLABELLED_IMAGES')
        from argparse import Namespace
        local = Namespace(**vars(args));local.dataset=str(root);local.command='annotate'
        local.replace=False;local.delete_unannotated=False;local.variations_enabled=False
        annotate(local)
        model_sha=getattr(local,'annotation_model_sha256',None)
        if type(model_sha) is not str or re.fullmatch('[0-9a-f]{64}',model_sha) is None:
            raise PlatformError('MODEL_IDENTITY_UNAVAILABLE')
        if read_bytes(root/'classes.txt',1024*1024).decode().splitlines() != target['classNames']:
            raise PlatformError('MODEL_CLASS_MISMATCH')
        accepted = 0
        for item in records:
            label='labels/'+Path(item['image']).stem+'.txt'
            data=read_bytes(root/label,1024*1024)
            parsed=parse_labels(data,len(target['classNames']),target['task'])
            accepted += bool(parsed);item.update(label=label,labelSHA256=digest(data),labelCount=len(parsed))
        settings={name:getattr(args,name) for name in ('provider','passes','confidence','consensus_iou','nms_iou')}
        plan={'schema':1,'target':target,'records':records,'settings':settings,
            'modelSHA256':model_sha,
            'optionsFingerprint':args.options_fingerprint,'annotated':accepted,
            'variations':{'enabled':args.variations_enabled,'copies':args.variation_copies,
                'extra_copies':args.extra_copies,
                'strength':args.variation_strength,'effects':list(args.variation_effects)},
            'seed':secrets.randbits(64),
            'uploadVariations':args.variations_enabled and args.extra_copies}
        encoded=json.dumps(plan,ensure_ascii=False,separators=(',',':')).encode()
        if len(encoded)>MAX_JSON:raise PlatformError('PREVIEW_LIMIT_EXCEEDED')
        write_new(root/'platform-preview.json',encoded)
        emit('cloud_preview_ready',folder=str(root),planSHA256=digest(read_bytes(root/'platform-preview.json',MAX_JSON)),
            sources=len(records),annotated=accepted,
            generated=accepted*args.variation_copies if args.variations_enabled and args.extra_copies else 0,
            replace=accepted if args.variations_enabled and not args.extra_copies else 0,
            mode='copies' if args.extra_copies else 'overwrite',seed=plan['seed'])
        return root
    finally:
        client.close()


def labels_text(labels, classes, task):
    rows=[]
    if type(labels) is not list or len(labels)>10000:
        raise PlatformError('INVALID_ACCEPTED_LABELS')
    for item in labels:
        primary='bbox' if task=='detect' else 'segments'
        if (type(item) is not dict or not set(item)<= {'classId','bbox','segments'}
                or primary not in item or type(item.get('classId')) is not int):
            raise PlatformError('INVALID_ACCEPTED_LABELS')
        coords=item[primary]
        if type(coords) is not list or any(type(v) not in (int,float) or not math.isfinite(v) for v in coords):
            raise PlatformError('INVALID_ACCEPTED_LABELS')
        secondary='segments' if primary=='bbox' else 'bbox'
        if secondary in item:
            values=item[secondary]
            if type(values) is not list or any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
                raise PlatformError('INVALID_ACCEPTED_LABELS')
            auxiliary=(str(item['classId'])+' '+' '.join(str(v) for v in values)+'\n').encode()
            parse_labels(auxiliary,classes,'segment' if secondary=='segments' else 'detect')
        rows.append(str(item['classId'])+' '+' '.join(f'{v:.5f}' for v in coords)+'\n')
    data=''.join(rows).encode();parse_labels(data,classes,task)
    return data


class _ProducedArchive:
    """Unseekable ZIP sink: receipt hashes producer writes, never mutable readback."""
    def __init__(self,path):
        self.path=plain(path);self.size=0;self.digest=hashlib.sha256()
        self._stream=self.path.open('xb')

    def __enter__(self):return self

    def write(self,value):
        value=bytes(value)
        if self.size+len(value)>MAX_ARCHIVE:raise PlatformError('VARIANT_ARCHIVE_TOO_LARGE')
        if self._stream.write(value)!=len(value):raise OSError('Archive producer write was incomplete.')
        self.digest.update(value);self.size+=len(value)
        return len(value)

    def tell(self):return self.size
    def seek(self,*_args):raise OSError('Archive producer is unseekable.')
    def seekable(self):return False
    def flush(self):self._stream.flush()

    def __exit__(self,error_type,*_rest):
        try:
            if error_type is None:
                self.flush();os.fsync(self._stream.fileno())
        finally:self._stream.close()


def _verified_variation_manifest(folder,producer_bytes,context,copies,source_records):
    """Bind disk readback to producer bytes and accepted source/label membership."""
    if type(producer_bytes) is not bytes or not 0<len(producer_bytes)<=MAX_JSON:
        raise PlatformError('VARIATIONS_CHANGED')
    raw=read_bytes(folder/'result.json',MAX_JSON)
    if digest(raw)!=digest(producer_bytes):raise PlatformError('VARIATIONS_CHANGED')
    manifest=json.loads(producer_bytes)
    expected={}
    for index,name in enumerate(sorted(context)):
        for number in range(1,copies+1):
            stem=f'source-{index:06d}-variation-{number:02d}'
            expected['images/'+stem+'.png']=(name,'labels/'+stem+'.txt')
    rows=manifest.get('files')
    if (manifest.get('schema')!=1 or manifest.get('complete') is not True
            or manifest.get('sources')!=len(context) or manifest.get('generated')!=len(expected)
            or type(rows) is not list or len(rows)!=len(expected)):
        raise PlatformError('VARIATIONS_CHANGED')
    seen=set()
    for row in rows:
        image=row.get('image') if type(row) is dict else None
        if type(image) is not str or image not in expected or image in seen:
            raise PlatformError('VARIATIONS_CHANGED')
        seen.add(image);name,label=expected[image];accepted=context[name];source=source_records[name]
        if (row.get('label')!=label or row.get('sourceImage')!=name or row.get('original') is not False
                or row.get('labelSHA256')!=accepted['labelSHA256']
                or row.get('sourceSHA256')!=accepted['image']['sha256']
                or row.get('width')!=source['width'] or row.get('height')!=source['height']
                or type(row.get('imageSHA256')) is not str
                or re.fullmatch(r'[0-9a-f]{64}',row['imageSHA256']) is None):
            raise PlatformError('VARIATIONS_CHANGED')
    return manifest


def replace_cloud_images(root,plan,confirmed,client,record,*,emit=lambda *_a,**_kw:None,progress=None):
    """Upload and verify new records before deleting exact original IDs, once.

    A stopped job keeps its source bytes, accepted labels, archive and durable
    mapping. It may leave both records, but never removes an unverified source.
    New records have new IDs/filenames; labels, split and custom metadata persist.
    """
    from annotation_variations import effect_seed,vary_image
    from annotation_output import encode_replacement
    import xxhash
    progress=progress or Progress(emit)
    target=plan['target'];options=VariationOptions(**{**plan['variations'],'effects':tuple(plan['variations']['effects'])}).validate()
    output=new_output(root,'vaporlock-replacement');prefix='vl-replace-'+output.name.rsplit('-',1)[1]
    rows=[];metadata={};total=0
    for image_index,(item,accepted_bytes) in enumerate(confirmed,1):
        progress('effects',current=image_index-1,total=len(confirmed))
        source=read_bytes(root/item['image'])
        if digest(source)!=item['sourceSHA256']:raise PlatformError('PREVIEW_CHANGED')
        snapshot=client.image_snapshot(target,item['id']);props=snapshot['properties']
        if (any(props.get(k)!=item[k] for k in ('hash','width','height','split'))
                or props.get('updatedAt')!=item.get('updatedAt') or snapshot['labels']):
            raise PlatformError('IMAGE_STATE_CHANGED')
        image=decode_image(source);extension=Path(item['image']).suffix.lower()
        varied,effects=vary_image(image,options,effect_seed(plan['seed'],Path(item['image']).name,overwrite=True))
        data=encode_replacement(source,varied,extension)
        if decode_image(data).tobytes()==image.tobytes():
            if client.dataset(target['owner'],target['dataset'])!=target:raise PlatformError('TARGET_CHANGED')
            if client.image_snapshot(target,item['id'])!=snapshot:raise PlatformError('IMAGE_STATE_CHANGED')
            labels=parse_labels(accepted_bytes,len(target['classNames']),target['task'])
            record('pending',id=item['id'])
            accepted=client.set_labels(item['id'],labels,expected_target=target,expected_image=props)
            record('confirmed',id=item['id'],labelsSHA256=digest(labels_text(accepted,len(target['classNames']),target['task'])))
            record('replacement_unchanged',id=item['id']);progress('effects',current=image_index,total=len(confirmed));continue
        # Platform re-encodes JPEG/PNG (quality 92), but retains WebP when no
        # resize/color conversion is needed. Preserve the exact preview pixels
        # in lossless RGB WebP instead of tolerating an approximate replacement.
        from io import BytesIO
        encoded_pixels=decode_image(data)
        buffer=BytesIO();encoded_pixels.save(buffer,format='WEBP',lossless=True,method=4)
        data=buffer.getvalue()
        if len(data)>MAX_IMAGE_BYTES or decode_image(data).tobytes()!=encoded_pixels.tobytes():
            raise PlatformError('REPLACEMENT_ENCODING_FAILED')
        name=prefix+'-'+item['id']+'.webp';path='images/'+item['split']+'/'+name
        total+=len(data)+len(accepted_bytes)
        if total>MAX_ARCHIVE:raise PlatformError('VARIANT_ARCHIVE_TOO_LARGE')
        write_new(output/'images'/name,data);write_new(output/'labels'/(Path(name).stem+'.txt'),accepted_bytes)
        metadata[path]=snapshot['metadata']
        if len(json.dumps(metadata,allow_nan=False))>500000:raise PlatformError('INVALID_IMAGE_METADATA')
        rows.append({'source':item,'snapshot':snapshot,'name':name,'path':path,'imageSHA256':digest(data),
            'hash':xxhash.xxh3_128_hexdigest(data,seed=0),'labelSHA256':digest(accepted_bytes),'effects':effects})
        progress('effects',current=image_index,total=len(confirmed))
    if not rows:return
    manifest=json.dumps({'schema':1,'target':target,'mode':'verified-upload-delete','records':rows},allow_nan=False).encode()
    if len(manifest)>MAX_JSON:raise PlatformError('PREVIEW_LIMIT_EXCEEDED')
    write_new(output/'replacement-plan.json',manifest)
    archive=output/'replacement.zip'
    progress('archive',current=0,total=len(rows),force=True)
    with _ProducedArchive(archive) as producer,zipfile.ZipFile(producer,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as package:
        for index,row in enumerate(rows,1):
            data=read_bytes(output/'images'/row['name']);label=read_bytes(output/'labels'/(Path(row['name']).stem+'.txt'),1024*1024)
            if digest(data)!=row['imageSHA256'] or digest(label)!=row['labelSHA256']:raise PlatformError('VARIATIONS_CHANGED')
            package.writestr(row['path'],data)
            package.writestr('labels/'+row['source']['split']+'/'+Path(row['name']).stem+'.txt',label)
            progress('archive',current=index,total=len(rows))
        package.writestr('data.yaml','train: images/train\nval: images/val\ntest: images/test\nnames:\n'+''.join(
            f'  {i}: {json.dumps(n)}\n' for i,n in enumerate(target['classNames'])))
    record('replacement_prepared',folder=output.name,images=len(rows),archiveSHA256=producer.digest.hexdigest(),planSHA256=digest(manifest))
    if client.dataset(target['owner'],target['dataset'])!=target:raise PlatformError('TARGET_CHANGED')
    job_id=client.ingest_variants(target,archive,expected_sha256=producer.digest.hexdigest(),image_metadata=metadata,record=record)
    record('ingest_queued',jobId=job_id,images=len(rows),mode='replacement')
    client.wait_ingest(target,job_id)
    entries=client.replacement_images(target,prefix,len(rows));by_name={}
    progress('inspect',current=0,total=len(rows),force=True)
    for index,entry in enumerate(entries,1):
        snapshot=client.image_snapshot(target,identifier(entry.get('id')))
        name=snapshot['properties'].get('filename')
        if name in by_name:raise PlatformError('REPLACEMENT_MEMBERSHIP_CHANGED')
        by_name[name]=(entry,snapshot)
        progress('inspect',current=index,total=len(rows))
    if set(by_name)!={row['name'] for row in rows}:raise PlatformError('REPLACEMENT_IMPORT_INCOMPLETE')
    # Verify every imported record before the first delete. Queued != completed.
    progress('verify',current=0,total=len(rows),force=True)
    for index,row in enumerate(rows,1):
        entry,snapshot=by_name[row['name']];new_id=identifier(entry['id'])
        props=snapshot['properties'];source=row['source'];accepted=parse_labels(
            read_bytes(output/'labels'/(Path(row['name']).stem+'.txt'),1024*1024),len(target['classNames']),target['task'])
        if (new_id==source['id'] or props.get('filename')!=row['name'] or props.get('hash')!=row['hash']
                or any(props.get(k)!=source[k] for k in ('width','height','split'))
                or snapshot['metadata']!=row['snapshot']['metadata']
                or not geometry_corresponds(accepted,snapshot['labels'],source['width'],source['height'])):
            raise PlatformError('REPLACEMENT_VERIFICATION_FAILED')
        pixels=client.download(entry.get('imageUrl'))
        if digest(pixels)!=row['imageSHA256']:raise PlatformError('REPLACEMENT_VERIFICATION_FAILED')
        row['replacementId']=new_id;row['replacementSnapshot']=snapshot
        record('replacement_verified',id=source['id'],replacementId=new_id,imageSHA256=row['imageSHA256'])
        progress('verify',current=index,total=len(rows))
    progress('delete',current=0,total=len(rows),force=True)
    for index,row in enumerate(rows,1):
        source=row['source'];new_id=row['replacementId']
        if client.dataset(target['owner'],target['dataset'])!=target:raise PlatformError('TARGET_CHANGED')
        if (client.image_snapshot(target,source['id'])!=row['snapshot']
                or client.image_snapshot(target,new_id)!=row['replacementSnapshot']):
            raise PlatformError('IMAGE_STATE_CHANGED')
        record('original_delete_pending',id=source['id'],replacementId=new_id)
        try:client.delete_image(source['id'])
        except MutationUncertain:
            record('original_delete_uncertain',id=source['id'],replacementId=new_id)
            raise
        record('original_deleted',id=source['id'],replacementId=new_id)
        emit('cloud_progress',stage='replace',current=index,total=len(rows))
        progress('delete',current=index,total=len(rows))
    record('replacement_complete',replaced=len(rows),jobId=job_id)


def apply_preview(args, key, *, client_factory=PlatformClient, emit=lambda *_a,**_kw:None):
    # The native client admits this command only after a default-Cancel warning
    # for this exact private plan. No renderer-provided path or credential.
    root=plain(args.dataset);raw=read_bytes(root/'platform-preview.json',MAX_JSON)
    if digest(raw)!=args.plan_sha256:
        raise PlatformError('PREVIEW_CHANGED')
    plan=json.loads(raw);target=plan['target'];records=plan['records']
    if (plan.get('schema')!=1 or type(records) is not list or not 1<=len(records)<=MAX_IMAGES
            or plan.get('optionsFingerprint')!=args.options_fingerprint):
        raise PlatformError('PREVIEW_CHANGED')
    variation=VariationOptions(**{**plan['variations'],'effects':tuple(plan['variations']['effects'])}).validate()
    for item in records:
        image_id=identifier(item['id'])
        if (item['image'] not in ('images/'+image_id+'.'+ext for ext in ('jpg','jpeg','png','bmp','webp'))
                or item['label']!='labels/'+image_id+'.txt'
                or digest(read_bytes(root/item['image']))!=item['sourceSHA256']
                or digest(read_bytes(root/item['label'],1024*1024))!=item['labelSHA256']):
            raise PlatformError('PREVIEW_CHANGED')
        parse_labels(read_bytes(root/item['label'],1024*1024),len(target['classNames']),target['task'])
    receipt=root/'platform-apply.jsonl'
    if receipt.exists():
        raise PlatformError('PREVIEW_ALREADY_APPLIED')
    client=client_factory(key);confirmed=[];skipped=0;unknown=0;queued=0;label_updates=0
    direct_replacement=variation.enabled and not variation.extra_copies
    progress=Progress(emit);progress('check',current=0,total=len(records),force=True)
    upload_state={};replacement_state={'replaced':0,'uploaded':0}
    if isinstance(client,PlatformClient):
        client._check_cancel=getattr(args,'check_cancel',lambda:None);client._progress=progress
    # Hold the root identity and exclusive receipt for the entire apply. Each
    # attempted mutation is flushed BEFORE send; crash therefore remains unknown.
    with held_directory(root),receipt.open('xb') as log:
        def record(event,**fields):
            nonlocal label_updates
            log.write((json.dumps({'event':event,**fields},allow_nan=False)+'\n').encode());log.flush()
            import os
            os.fsync(log.fileno())
            # Durable phase facts stay separate from uncertain remote outcomes.
            if event=='upload_session_ready':upload_state.update(sessionCreated=True,storageConfirmed=False,sessionCompleted=False,ingestQueued=False)
            elif event=='storage_put_confirmed':upload_state['storageConfirmed']=True
            elif event=='upload_complete_confirmed':upload_state['sessionCompleted']=True
            elif event=='ingest_queued':upload_state['ingestQueued']=True
            elif event=='replacement_verified':replacement_state['uploaded']+=1
            elif event=='original_deleted':replacement_state['replaced']+=1
            elif event=='confirmed':label_updates+=1
        try:
            for item in records:
                if direct_replacement:
                    label_data=read_bytes(root/item['label'],1024*1024)
                    if digest(label_data)!=item['labelSHA256']:raise PlatformError('PREVIEW_CHANGED')
                    if not parse_labels(label_data,len(target['classNames']),target['task']):
                        skipped+=1;record('skipped',id=item['id']);continue
                    confirmed.append((item,label_data));record('replacement_annotation_planned',id=item['id'])
                    progress('check',current=len(confirmed)+skipped,total=len(records))
                    continue
                progress('labels',current=len(confirmed)+skipped,total=len(records))
                if client.dataset(target['owner'],target['dataset'])!=target:
                    raise PlatformError('TARGET_CHANGED')
                props,existing=client.full_image(target,item['id'])
                if (existing or props['hash']!=item['hash'] or props['split']!=item['split']
                        or props['width']!=item['width'] or props['height']!=item['height']
                        or props.get('updatedAt')!=item.get('updatedAt')):
                    skipped+=1;record('skipped',id=item['id']);continue
                # Validate and consume the SAME immutable byte buffer. A later
                # file edit cannot substitute labels between a hash and PATCH.
                label_data=read_bytes(root/item['label'],1024*1024)
                if digest(label_data)!=item['labelSHA256']:raise PlatformError('PREVIEW_CHANGED')
                labels=parse_labels(label_data,len(target['classNames']),target['task'])
                if not labels:
                    skipped+=1;record('skipped',id=item['id']);continue
                record('pending',id=item['id'])
                try:
                    accepted=client.set_labels(item['id'],labels,expected_target=target,expected_image=props)
                    try:
                        accepted_bytes=labels_text(accepted,len(target['classNames']),target['task'])
                    except (ValueError,PlatformError):
                        raise MutationUncertain() from None
                except MutationUncertain:
                    unknown+=1
                    # Read-only reconciliation; never resubmit or roll back.
                    try:
                        _,current=client.full_image(target,item['id'])
                        record('reconciliation',id=item['id'],labelsMatch=labels_text(current,len(target['classNames']),target['task'])==labels_text(labels,len(target['classNames']),target['task']))
                    except Exception:
                        record('reconciliation_unavailable',id=item['id'])
                    raise
                confirmed.append((item,accepted_bytes));record('confirmed',id=item['id'],labelsSHA256=digest(accepted_bytes))
                emit('cloud_progress',stage='apply',current=len(confirmed),total=plan['annotated'])
            if variation.enabled and not variation.extra_copies and confirmed:
                replace_cloud_images(root,plan,confirmed,client,record,emit=emit,progress=progress)
            if plan['uploadVariations'] and plan['variations']['enabled'] and confirmed:
                from annotation_output import generate_variations
                dataset=new_output(root,'vaporlock-platform-accepted')
                write_new(dataset/'classes.txt', ('\n'.join(target['classNames'])+'\n').encode())
                splits={};expected_context={};source_records={}
                for item,accepted_bytes in confirmed:
                    name=Path(item['image']).name
                    source_bytes=read_bytes(root/item['image'])
                    if digest(source_bytes)!=item['sourceSHA256']:raise PlatformError('PREVIEW_CHANGED')
                    write_new(dataset/'images'/name,source_bytes)
                    write_new(dataset/'labels'/Path(item['label']).name,accepted_bytes)
                    splits[name]=item['split']
                    from dataset_transaction import snapshot_file
                    snapshot=snapshot_file(dataset/'images'/name)
                    if snapshot['sha256']!=item['sourceSHA256']:raise PlatformError('PREVIEW_CHANGED')
                    expected_context[name]={'image':snapshot,'labelSHA256':digest(accepted_bytes)}
                    source_records[name]=item
                options=VariationOptions(**{**plan['variations'],'effects':tuple(plan['variations']['effects'])})
                varied,producer_manifest=generate_variations(dataset,options,seed=plan['seed'],expected_context=expected_context,
                    expected_classes_sha256=digest(('\n'.join(target['classNames'])+'\n').encode()),return_receipt=True,emit=emit)
                manifest=_verified_variation_manifest(varied,producer_manifest,expected_context,options.copies,source_records)
                archive=root/'ultralytics-variations.zip'
                with _ProducedArchive(archive) as producer,zipfile.ZipFile(producer,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as output:
                    for item in manifest['files']:
                        if item['original']:continue
                        split=splits[item['sourceImage']]
                        image_data=read_bytes(varied/item['image']);label_data=read_bytes(varied/item['label'],1024*1024)
                        if digest(image_data)!=item['imageSHA256'] or digest(label_data)!=item['labelSHA256']:
                            raise PlatformError('VARIATIONS_CHANGED')
                        unique=varied.name+'-'+Path(item['image']).name
                        output.writestr('images/'+split+'/'+unique,image_data)
                        output.writestr('labels/'+split+'/'+Path(unique).stem+'.txt',label_data)
                    output.writestr('data.yaml','train: images/train\nval: images/val\ntest: images/test\nnames:\n'+''.join(f'  {i}: {json.dumps(n)}\n' for i,n in enumerate(target['classNames'])))
                if client.dataset(target['owner'],target['dataset'])!=target:
                    raise PlatformError('TARGET_CHANGED')
                # Stream the bounded archive hash; image transaction snapshots
                # deliberately have a smaller per-file limit than upload ZIPs.
                from dataset_transaction import HeldFile
                archive_hash=hashlib.sha256();total=0
                with held_directory(archive.parent),HeldFile(archive) as held:
                    info=held.info()
                    if ((info.size_high<<32)|info.size_low)!=producer.size:raise PlatformError('VARIATIONS_CHANGED')
                    for block in held.blocks():
                        total+=len(block)
                        if total>MAX_ARCHIVE:raise PlatformError('VARIANT_ARCHIVE_TOO_LARGE')
                        archive_hash.update(block)
                produced_hash=producer.digest.hexdigest()
                if archive_hash.hexdigest()!=produced_hash:raise PlatformError('VARIATIONS_CHANGED')
                record('upload_pending',archiveSHA256=produced_hash)
                job_id=client.ingest_variants(target,archive,expected_sha256=produced_hash,record=record);queued=manifest['generated']
                record('ingest_queued',jobId=job_id,images=queued)
            phases={'uploadState':dict(upload_state)} if upload_state else {}
            if variation.enabled and not variation.extra_copies:phases['replacementState']=dict(replacement_state)
            committed=label_updates+(replacement_state['uploaded'] if direct_replacement else 0)
            record('complete',confirmed=committed,skipped=skipped,queued=queued,unknown=unknown,**phases)
            emit('cloud_complete',confirmed=committed,skipped=skipped,queued=queued,unknown=unknown,**phases)
            return 0
        except Exception as error:
            if isinstance(error,MutationUncertain) and not unknown:unknown=1
            phases={'uploadState':dict(upload_state)} if upload_state else {}
            if variation.enabled and not variation.extra_copies:phases['replacementState']=dict(replacement_state)
            committed=label_updates+(replacement_state['uploaded'] if direct_replacement else 0)
            code=error.code if isinstance(error,PlatformError) else 'LOCAL_OPERATION_FAILED'
            record('stopped',confirmed=committed,skipped=skipped,queued=queued,unknown=unknown,code=code,**phases)
            emit('cloud_stopped',confirmed=committed,skipped=skipped,queued=queued,unknown=unknown,code=code,**phases)
            raise
        finally:
            client.close()
