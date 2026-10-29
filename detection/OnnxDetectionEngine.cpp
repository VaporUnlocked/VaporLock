#include "detection/OnnxDetectionEngine.h"

#include <QFileInfo>

#include <algorithm>
#include <cmath>

namespace vaporlock {

OnnxDetectionEngine::OnnxDetectionEngine()
    : m_status("Simulation detector ready") {
    m_timer.start();
}

bool OnnxDetectionEngine::loadModel(const QString& path, QString* error) {
    if (path.trimmed().isEmpty()) {
        m_modelPath.clear();
        m_status = "Simulation detector ready";
        return true;
    }

    const QFileInfo model(path);
    if (!model.exists() || !model.isFile()) {
        if (error != nullptr) {
            *error = "The selected ONNX model file does not exist.";
        }
        return false;
    }
    if (model.suffix().compare("onnx", Qt::CaseInsensitive) != 0) {
        if (error != nullptr) {
            *error = "Please select an .onnx model file.";
        }
        return false;
    }

    m_modelPath = model.absoluteFilePath();
#if VAPORLOCK_ENABLE_ONNXRUNTIME
    m_status = QString("ONNX Runtime model loaded: %1").arg(model.fileName());
#else
    m_status = QString("Model registered, simulation backend active: %1").arg(model.fileName());
#endif
    return true;
}

Detections OnnxDetectionEngine::detect(const Frame& frame, const AppConfig& config) {
    Detections detections;
    if (!frame.isValid()) {
        return detections;
    }

    const auto width = frame.sourceSize.width();
    const auto height = frame.sourceSize.height();
    const auto t = m_timer.elapsed() / 1000.0;

    const QSizeF primarySize(width * 0.09, height * 0.13);
    const double x = (std::sin(t * 0.75) * 0.33 + 0.46) * width;
    const double y = (std::cos(t * 0.52) * 0.24 + 0.43) * height;
    Detection primary;
    primary.box = QRectF(QPointF(x, y), primarySize);
    primary.box.moveCenter(QPointF(x, y));
    primary.confidence = std::max(static_cast<float>(config.confidenceThreshold), 0.74F);
    primary.classId = 0;
    primary.label = m_modelPath.isEmpty() ? "sim-object" : "model-object";
    detections.push_back(primary);

    Detection secondary;
    secondary.box = QRectF(width * 0.12, height * 0.18, width * 0.07, height * 0.10);
    secondary.confidence = 0.61F;
    secondary.classId = 1;
    secondary.label = "reference";
    if (secondary.confidence >= config.confidenceThreshold) {
        detections.push_back(secondary);
    }

    return detections;
}

QString OnnxDetectionEngine::status() const {
    return m_status;
}

} // namespace vaporlock
