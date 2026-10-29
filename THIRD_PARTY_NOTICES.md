# Third-party rights

The noncommercial license applies only to original material that VaporLock's
copyright holders have authority to license. Keep all existing vendor LICENSE,
COPYING, NOTICE and attribution files. They take precedence for that material.
The included Qt qwebchannel.js is used under its upstream LGPL-3.0 option.
Full LGPL-3.0 and its incorporated GPL-3.0 text accompany it under
third_party/qt/. The VaporLock noncommercial terms do not restrict the rights
these licenses grant in Qt or other separately licensed material. Distributions
of Qt libraries must also satisfy their applicable source/relinking and notice
obligations; no official-build signature is required to rebuild this client.
The public source export excludes private models, datasets, credentials and
SDK/runtime binaries; do not upload the workspace itself or its build history.

Dependencies include Qt/PySide, ONNX Runtime, DirectML, PyTorch, CUDA/TensorRT,
OpenCV, SAM/TinyViT and controller components. These are independent projects;
their licenses and redistribution conditions must be checked for the exact
versions and build you distribute. Downloading a dependency is not permission
to relicense it. Obtain SDKs and redistributables from their original publishers.

Ultralytics integrations are optional and include AGPL-licensed packages unless
you have a separate applicable license. A noncommercial restriction cannot be
added to AGPL-covered material. Whether a combined distribution constitutes a
covered work depends on the actual integration; this file does not resolve that
question or waive those obligations. Review the integration's licensing before
distributing a combined build. This export does not represent that merely
separating files or omitting dependency binaries resolves license compatibility.

Official sources:
- https://www.ultralytics.com/license
- https://www.gnu.org/licenses/gpl-faq.html
- https://doc.qt.io/qtforpython-6/licenses.html
- https://github.com/microsoft/onnxruntime/blob/main/LICENSE

The source export and its private verification report distinguish absent binary
dependencies from included source. Never replace upstream notices with the
VaporLock license or claim ownership of third-party code or community datasets.
