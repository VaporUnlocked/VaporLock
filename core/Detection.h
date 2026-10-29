#pragma once

#include <QPointF>
#include <QMetaType>
#include <QRectF>
#include <QString>

#include <optional>
#include <vector>

namespace vaporlock {

struct Detection {
    QRectF box;
    float confidence = 0.0F;
    int classId = 0;
    QString label;
};

struct Track {
    int id = 0;
    QRectF box;
    QPointF velocity;
    float confidence = 0.0F;
    int classId = 0;
    QString label;
    int lostFrames = 0;
    bool selected = false;
};

using Detections = std::vector<Detection>;
using Tracks = std::vector<Track>;
using SelectedTrack = std::optional<Track>;

} // namespace vaporlock

Q_DECLARE_METATYPE(vaporlock::Detection)
Q_DECLARE_METATYPE(vaporlock::Track)
Q_DECLARE_METATYPE(vaporlock::Tracks)
Q_DECLARE_METATYPE(vaporlock::SelectedTrack)
