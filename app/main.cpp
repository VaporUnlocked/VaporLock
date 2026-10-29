#include "core/ApplicationController.h"
#include "gui/MainWindow.h"

#include <QApplication>
#include <QSurfaceFormat>

int main(int argc, char* argv[]) {
    QApplication app(argc, argv);
    QApplication::setApplicationName("Vapor Lock");
    QApplication::setOrganizationName("Vapor Lock");

    QSurfaceFormat format;
    format.setSamples(4);
    QSurfaceFormat::setDefaultFormat(format);

    qRegisterMetaType<vaporlock::Frame>("vaporlock::Frame");
    qRegisterMetaType<vaporlock::Tracks>("vaporlock::Tracks");
    qRegisterMetaType<vaporlock::SelectedTrack>("vaporlock::SelectedTrack");
    qRegisterMetaType<vaporlock::RuntimeStats>("vaporlock::RuntimeStats");
    qRegisterMetaType<vaporlock::AppConfig>("vaporlock::AppConfig");

    vaporlock::ApplicationController controller;
    vaporlock::MainWindow window(&controller);
    window.show();
    return app.exec();
}
