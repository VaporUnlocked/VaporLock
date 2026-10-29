#pragma once

#include "config/AppConfig.h"
#include "core/ApplicationController.h"

#include <QLabel>
#include <QMainWindow>
#include <QPlainTextEdit>
#include <QPointer>
#include <QTimer>

class QCheckBox;
class QComboBox;
class QDoubleSpinBox;
class QFormLayout;
class QPushButton;
class QSlider;
class QSpinBox;

namespace vaporlock {

class PreviewWidget final : public QWidget {
    Q_OBJECT

public:
    explicit PreviewWidget(QWidget* parent = nullptr);
    void setOverlayEnabled(bool enabled);

public slots:
    void setFrame(const vaporlock::Frame& frame, const vaporlock::Tracks& tracks, const vaporlock::SelectedTrack& selected);

protected:
    void paintEvent(QPaintEvent* event) override;

private:
    Frame m_frame;
    Tracks m_tracks;
    SelectedTrack m_selected;
    bool m_overlayEnabled = true;
};

class MainWindow final : public QMainWindow {
    Q_OBJECT

public:
    explicit MainWindow(ApplicationController* controller, QWidget* parent = nullptr);

private slots:
    void startStop();
    void chooseModel();
    void loadProfile();
    void saveProfile();
    void saveProfileAs();
    void updateStats(const vaporlock::RuntimeStats& stats);
    void appendLog(const QString& subsystem, const QString& message);
    void refreshFromConfig(const vaporlock::AppConfig& config);
    void pushConfig();

private:
    QWidget* buildSettingsPanel();
    QWidget* buildProfilesPanel();
    QWidget* buildStatsPanel();
    void createDocks();
    void styleApplication();

    ApplicationController* m_controller = nullptr;
    PreviewWidget* m_preview = nullptr;
    QLabel* m_statusLabel = nullptr;
    QLabel* m_profileLabel = nullptr;
    QPlainTextEdit* m_log = nullptr;
    QPushButton* m_startButton = nullptr;

    QDoubleSpinBox* m_confidence = nullptr;
    QDoubleSpinBox* m_iou = nullptr;
    QSpinBox* m_captureFps = nullptr;
    QSpinBox* m_maxLost = nullptr;
    QDoubleSpinBox* m_smoothing = nullptr;
    QComboBox* m_strategy = nullptr;
    QSpinBox* m_acquisition = nullptr;
    QSpinBox* m_retention = nullptr;
    QDoubleSpinBox* m_hysteresis = nullptr;
    QCheckBox* m_movementEnabled = nullptr;
    QDoubleSpinBox* m_gain = nullptr;
    QSpinBox* m_maxStep = nullptr;
    QComboBox* m_activation = nullptr;
    QCheckBox* m_overlay = nullptr;

    QLabel* m_fpsLabel = nullptr;
    QLabel* m_latencyLabel = nullptr;
    QLabel* m_countsLabel = nullptr;
    QLabel* m_detectorLabel = nullptr;
    bool m_refreshing = false;
};

} // namespace vaporlock
