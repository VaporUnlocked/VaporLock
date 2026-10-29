#include "gui/MainWindow.h"

#include <QApplication>
#include <QCheckBox>
#include <QComboBox>
#include <QDateTime>
#include <QDockWidget>
#include <QDoubleSpinBox>
#include <QFileDialog>
#include <QFormLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QMenuBar>
#include <QMessageBox>
#include <QPainter>
#include <QPushButton>
#include <QScreen>
#include <QSpinBox>
#include <QStatusBar>
#include <QToolBar>
#include <QVBoxLayout>

#include <algorithm>

namespace vaporlock {

PreviewWidget::PreviewWidget(QWidget* parent)
    : QWidget(parent) {
    setMinimumSize(860, 520);
    setAutoFillBackground(false);
}

void PreviewWidget::setOverlayEnabled(bool enabled) {
    m_overlayEnabled = enabled;
    update();
}

void PreviewWidget::setFrame(const Frame& frame, const Tracks& tracks, const SelectedTrack& selected) {
    m_frame = frame;
    m_tracks = tracks;
    m_selected = selected;
    update();
}

void PreviewWidget::paintEvent(QPaintEvent* event) {
    Q_UNUSED(event);

    QPainter painter(this);
    painter.setRenderHint(QPainter::Antialiasing);
    painter.fillRect(rect(), QColor(12, 16, 24));

    if (!m_frame.isValid()) {
        painter.setPen(QColor(190, 203, 220));
        painter.drawText(rect(), Qt::AlignCenter, "Preview will appear when the pipeline starts");
        return;
    }

    const auto scaled = m_frame.image.scaled(size(), Qt::KeepAspectRatio, Qt::SmoothTransformation);
    const QPoint origin((width() - scaled.width()) / 2, (height() - scaled.height()) / 2);
    painter.drawImage(origin, scaled);

    if (!m_overlayEnabled) {
        return;
    }

    const double sx = scaled.width() / static_cast<double>(m_frame.sourceSize.width());
    const double sy = scaled.height() / static_cast<double>(m_frame.sourceSize.height());
    painter.setFont(QFont("Segoe UI", 9));

    for (const auto& track : m_tracks) {
        QRectF box(origin.x() + track.box.x() * sx, origin.y() + track.box.y() * sy, track.box.width() * sx, track.box.height() * sy);
        const bool selected = m_selected.has_value() && m_selected->id == track.id;
        painter.setPen(QPen(selected ? QColor(255, 204, 92) : QColor(50, 220, 180), selected ? 3 : 2));
        painter.drawRect(box);

        const QString label = QString("#%1 %2 %3%").arg(track.id).arg(track.label).arg(static_cast<int>(track.confidence * 100.0F));
        const QRectF labelRect(box.left(), std::max(0.0, box.top() - 24.0), std::max(150.0, box.width()), 22.0);
        painter.fillRect(labelRect, QColor(5, 8, 13, 190));
        painter.setPen(QColor(242, 247, 252));
        painter.drawText(labelRect.adjusted(6, 0, -6, 0), Qt::AlignVCenter | Qt::AlignLeft, label);

        painter.setPen(QPen(QColor(255, 204, 92), 2));
        const auto c = box.center();
        painter.drawLine(c, c + QPointF(track.velocity.x() * sx * 8.0, track.velocity.y() * sy * 8.0));
    }

    painter.setPen(QPen(QColor(130, 170, 220, 150), 1));
    painter.drawLine(width() / 2, 0, width() / 2, height());
    painter.drawLine(0, height() / 2, width(), height() / 2);
}

MainWindow::MainWindow(ApplicationController* controller, QWidget* parent)
    : QMainWindow(parent)
    , m_controller(controller) {
    setWindowTitle("Vapor Lock");
    resize(1320, 820);
    styleApplication();

    m_preview = new PreviewWidget(this);
    setCentralWidget(m_preview);

    createDocks();

    auto* toolbar = addToolBar("Run");
    toolbar->setMovable(false);
    m_startButton = new QPushButton("Start", this);
    connect(m_startButton, &QPushButton::clicked, this, &MainWindow::startStop);
    toolbar->addWidget(m_startButton);

    auto* modelButton = new QPushButton("Load ONNX Model", this);
    connect(modelButton, &QPushButton::clicked, this, &MainWindow::chooseModel);
    toolbar->addWidget(modelButton);

    m_statusLabel = new QLabel("Ready", this);
    statusBar()->addPermanentWidget(m_statusLabel, 1);

    connect(m_controller->pipeline(), &FramePipeline::frameReady, m_preview, &PreviewWidget::setFrame);
    connect(m_controller->pipeline(), &FramePipeline::statsReady, this, &MainWindow::updateStats);
    connect(m_controller, &ApplicationController::logMessage, this, &MainWindow::appendLog);
    connect(m_controller, &ApplicationController::configChanged, this, &MainWindow::refreshFromConfig);
    connect(m_controller, &ApplicationController::profilePathChanged, this, [this](const QString& path) {
        m_profileLabel->setText(path);
    });

    refreshFromConfig(m_controller->config());
    m_profileLabel->setText(m_controller->profilePath());
    appendLog("Application", "Initialized");
}

void MainWindow::startStop() {
    if (m_controller->pipeline()->isRunning()) {
        m_controller->stop();
        m_startButton->setText("Start");
        m_statusLabel->setText("Stopped");
        return;
    }

    m_controller->start();
    m_startButton->setText("Stop");
    m_statusLabel->setText("Running");
}

void MainWindow::chooseModel() {
    const auto path = QFileDialog::getOpenFileName(this, "Load ONNX Model", QString(), "ONNX Models (*.onnx)");
    if (path.isEmpty()) {
        return;
    }

    QString error;
    if (!m_controller->loadModel(path, &error)) {
        QMessageBox::warning(this, "Model Load Failed", error);
        return;
    }
}

void MainWindow::loadProfile() {
    const auto path = QFileDialog::getOpenFileName(this, "Load Profile", QString(), "Vapor Lock Profiles (*.json)");
    if (!path.isEmpty()) {
        m_controller->loadProfile(path);
    }
}

void MainWindow::saveProfile() {
    m_controller->saveProfile(m_controller->profilePath());
}

void MainWindow::saveProfileAs() {
    const auto path = QFileDialog::getSaveFileName(this, "Save Profile As", m_controller->profilePath(), "Vapor Lock Profiles (*.json)");
    if (!path.isEmpty()) {
        m_controller->saveProfile(path);
    }
}

void MainWindow::updateStats(const RuntimeStats& stats) {
    m_fpsLabel->setText(QString("Pipeline %1 FPS | Capture %2 FPS").arg(stats.pipelineFps, 0, 'f', 1).arg(stats.captureFps, 0, 'f', 1));
    m_latencyLabel->setText(QString("Capture %1 ms | Detection %2 ms | Tracking %3 ms")
                                .arg(stats.captureLatencyMs, 0, 'f', 1)
                                .arg(stats.inferenceLatencyMs, 0, 'f', 1)
                                .arg(stats.trackingLatencyMs, 0, 'f', 1));
    m_countsLabel->setText(QString("%1 detections | %2 tracks | output %3")
                               .arg(stats.detectionCount)
                               .arg(stats.trackCount)
                               .arg(stats.outputActive ? "active" : "idle"));
    m_detectorLabel->setText(stats.detectorStatus);
}

void MainWindow::appendLog(const QString& subsystem, const QString& message) {
    const auto stamp = QDateTime::currentDateTime().toString("HH:mm:ss");
    m_log->appendPlainText(QString("[%1] [%2] %3").arg(stamp, subsystem, message));
}

void MainWindow::refreshFromConfig(const AppConfig& config) {
    m_refreshing = true;
    m_confidence->setValue(config.confidenceThreshold);
    m_iou->setValue(config.iouThreshold);
    m_captureFps->setValue(config.captureFps);
    m_maxLost->setValue(config.maxLostFrames);
    m_smoothing->setValue(config.smoothing);
    m_strategy->setCurrentText(toString(config.selectionStrategy));
    m_acquisition->setValue(config.acquisitionRadius);
    m_retention->setValue(config.retentionRadius);
    m_hysteresis->setValue(config.switchingHysteresis);
    m_movementEnabled->setChecked(config.movementEnabled);
    m_gain->setValue(config.movementGain);
    m_maxStep->setValue(config.maxMovementStep);
    m_activation->setCurrentText(toString(config.activationMode));
    m_overlay->setChecked(config.overlayEnabled);
    m_preview->setOverlayEnabled(config.overlayEnabled);
    m_refreshing = false;
}

void MainWindow::pushConfig() {
    if (m_refreshing) {
        return;
    }

    auto config = m_controller->config();
    config.confidenceThreshold = m_confidence->value();
    config.iouThreshold = m_iou->value();
    config.captureFps = m_captureFps->value();
    config.maxLostFrames = m_maxLost->value();
    config.smoothing = m_smoothing->value();
    config.selectionStrategy = selectionStrategyFromString(m_strategy->currentText());
    config.acquisitionRadius = m_acquisition->value();
    config.retentionRadius = m_retention->value();
    config.switchingHysteresis = m_hysteresis->value();
    config.movementEnabled = m_movementEnabled->isChecked();
    config.movementGain = m_gain->value();
    config.maxMovementStep = m_maxStep->value();
    config.activationMode = activationModeFromString(m_activation->currentText());
    config.overlayEnabled = m_overlay->isChecked();
    m_preview->setOverlayEnabled(config.overlayEnabled);
    m_controller->applyConfig(config);
}

QWidget* MainWindow::buildSettingsPanel() {
    auto* panel = new QWidget(this);
    auto* layout = new QVBoxLayout(panel);

    auto connectChange = [this](auto* widget) {
        connect(widget, qOverload<double>(&QDoubleSpinBox::valueChanged), this, &MainWindow::pushConfig);
    };

    auto* detection = new QGroupBox("Detection", panel);
    auto* detectionForm = new QFormLayout(detection);
    m_confidence = new QDoubleSpinBox(detection);
    m_confidence->setRange(0.01, 1.0);
    m_confidence->setSingleStep(0.01);
    m_iou = new QDoubleSpinBox(detection);
    m_iou->setRange(0.01, 1.0);
    m_iou->setSingleStep(0.01);
    detectionForm->addRow("Confidence", m_confidence);
    detectionForm->addRow("IoU", m_iou);
    connectChange(m_confidence);
    connectChange(m_iou);

    auto* capture = new QGroupBox("Capture", panel);
    auto* captureForm = new QFormLayout(capture);
    m_captureFps = new QSpinBox(capture);
    m_captureFps->setRange(1, 240);
    captureForm->addRow("FPS", m_captureFps);
    connect(m_captureFps, qOverload<int>(&QSpinBox::valueChanged), this, &MainWindow::pushConfig);

    auto* tracking = new QGroupBox("Tracking", panel);
    auto* trackingForm = new QFormLayout(tracking);
    m_maxLost = new QSpinBox(tracking);
    m_maxLost->setRange(1, 300);
    m_smoothing = new QDoubleSpinBox(tracking);
    m_smoothing->setRange(0.0, 0.95);
    m_smoothing->setSingleStep(0.05);
    trackingForm->addRow("Lost Frames", m_maxLost);
    trackingForm->addRow("Smoothing", m_smoothing);
    connect(m_maxLost, qOverload<int>(&QSpinBox::valueChanged), this, &MainWindow::pushConfig);
    connectChange(m_smoothing);

    auto* selection = new QGroupBox("Target Selection", panel);
    auto* selectionForm = new QFormLayout(selection);
    m_strategy = new QComboBox(selection);
    m_strategy->addItems({"center", "confidence", "largest", "smallest", "lowestMovement"});
    m_acquisition = new QSpinBox(selection);
    m_acquisition->setRange(1, 8000);
    m_retention = new QSpinBox(selection);
    m_retention->setRange(1, 8000);
    m_hysteresis = new QDoubleSpinBox(selection);
    m_hysteresis->setRange(0.0, 1.0);
    m_hysteresis->setSingleStep(0.01);
    selectionForm->addRow("Strategy", m_strategy);
    selectionForm->addRow("Acquire Radius", m_acquisition);
    selectionForm->addRow("Retain Radius", m_retention);
    selectionForm->addRow("Hysteresis", m_hysteresis);
    connect(m_strategy, &QComboBox::currentTextChanged, this, &MainWindow::pushConfig);
    connect(m_acquisition, qOverload<int>(&QSpinBox::valueChanged), this, &MainWindow::pushConfig);
    connect(m_retention, qOverload<int>(&QSpinBox::valueChanged), this, &MainWindow::pushConfig);
    connectChange(m_hysteresis);

    auto* movement = new QGroupBox("Movement Output", panel);
    auto* movementForm = new QFormLayout(movement);
    m_movementEnabled = new QCheckBox("Enabled", movement);
    m_gain = new QDoubleSpinBox(movement);
    m_gain->setRange(0.0, 4.0);
    m_gain->setSingleStep(0.01);
    m_maxStep = new QSpinBox(movement);
    m_maxStep->setRange(1, 500);
    m_activation = new QComboBox(movement);
    m_activation->addItems({"hold", "toggle"});
    movementForm->addRow(m_movementEnabled);
    movementForm->addRow("Gain", m_gain);
    movementForm->addRow("Max Step", m_maxStep);
    movementForm->addRow("Activation", m_activation);
    connect(m_movementEnabled, &QCheckBox::toggled, this, &MainWindow::pushConfig);
    connectChange(m_gain);
    connect(m_maxStep, qOverload<int>(&QSpinBox::valueChanged), this, &MainWindow::pushConfig);
    connect(m_activation, &QComboBox::currentTextChanged, this, &MainWindow::pushConfig);

    m_overlay = new QCheckBox("Show Overlay", panel);
    connect(m_overlay, &QCheckBox::toggled, this, &MainWindow::pushConfig);

    layout->addWidget(detection);
    layout->addWidget(capture);
    layout->addWidget(tracking);
    layout->addWidget(selection);
    layout->addWidget(movement);
    layout->addWidget(m_overlay);
    layout->addStretch(1);
    return panel;
}

QWidget* MainWindow::buildProfilesPanel() {
    auto* panel = new QWidget(this);
    auto* layout = new QVBoxLayout(panel);
    auto* row = new QHBoxLayout();

    auto* load = new QPushButton("Load", panel);
    auto* save = new QPushButton("Save", panel);
    auto* saveAs = new QPushButton("Save As", panel);
    connect(load, &QPushButton::clicked, this, &MainWindow::loadProfile);
    connect(save, &QPushButton::clicked, this, &MainWindow::saveProfile);
    connect(saveAs, &QPushButton::clicked, this, &MainWindow::saveProfileAs);
    row->addWidget(load);
    row->addWidget(save);
    row->addWidget(saveAs);

    m_profileLabel = new QLabel(panel);
    m_profileLabel->setWordWrap(true);
    layout->addLayout(row);
    layout->addWidget(m_profileLabel);
    layout->addStretch(1);
    return panel;
}

QWidget* MainWindow::buildStatsPanel() {
    auto* panel = new QWidget(this);
    auto* layout = new QVBoxLayout(panel);
    m_fpsLabel = new QLabel("Pipeline 0 FPS | Capture 0 FPS", panel);
    m_latencyLabel = new QLabel("Capture 0 ms | Detection 0 ms | Tracking 0 ms", panel);
    m_countsLabel = new QLabel("0 detections | 0 tracks | output idle", panel);
    m_detectorLabel = new QLabel("Detector initializing", panel);
    m_detectorLabel->setWordWrap(true);
    layout->addWidget(m_fpsLabel);
    layout->addWidget(m_latencyLabel);
    layout->addWidget(m_countsLabel);
    layout->addWidget(m_detectorLabel);
    layout->addStretch(1);
    return panel;
}

void MainWindow::createDocks() {
    auto* settingsDock = new QDockWidget("Configuration", this);
    settingsDock->setWidget(buildSettingsPanel());
    addDockWidget(Qt::LeftDockWidgetArea, settingsDock);

    auto* profileDock = new QDockWidget("Profiles", this);
    profileDock->setWidget(buildProfilesPanel());
    addDockWidget(Qt::RightDockWidgetArea, profileDock);

    auto* statsDock = new QDockWidget("Diagnostics", this);
    statsDock->setWidget(buildStatsPanel());
    addDockWidget(Qt::RightDockWidgetArea, statsDock);

    auto* logDock = new QDockWidget("Log", this);
    m_log = new QPlainTextEdit(logDock);
    m_log->setReadOnly(true);
    m_log->setMaximumBlockCount(2000);
    logDock->setWidget(m_log);
    addDockWidget(Qt::BottomDockWidgetArea, logDock);
}

void MainWindow::styleApplication() {
    qApp->setStyleSheet(R"(
        QMainWindow, QWidget { background: #101722; color: #e6edf5; font-family: Segoe UI; font-size: 10pt; }
        QDockWidget { titlebar-close-icon: none; titlebar-normal-icon: none; }
        QDockWidget::title { background: #172233; padding: 8px; font-weight: 600; }
        QGroupBox { border: 1px solid #2b3c55; border-radius: 6px; margin-top: 12px; padding: 10px; }
        QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #9ed7ff; }
        QPushButton { background: #246bfe; border: 0; border-radius: 6px; color: white; padding: 7px 12px; font-weight: 600; }
        QPushButton:hover { background: #3d7cff; }
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit {
            background: #0d1320; border: 1px solid #33445d; border-radius: 5px; color: #e6edf5; padding: 4px;
        }
        QCheckBox { spacing: 8px; }
        QStatusBar { background: #0d1320; }
    )");
}

} // namespace vaporlock
