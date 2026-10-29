#include "tracking/CentroidTracker.h"

#include <QLineF>

#include <algorithm>
#include <limits>
#include <unordered_set>

namespace vaporlock {

Tracks CentroidTracker::update(const Detections& detections, const AppConfig& config) {
    std::unordered_set<int> matchedTracks;
    for (std::size_t detectionIndex = 0; detectionIndex < detections.size(); ++detectionIndex) {
        const auto& detection = detections[detectionIndex];
        const QPointF center = detection.box.center();

        int bestId = 0;
        double bestDistance = std::numeric_limits<double>::max();
        for (auto& [id, state] : m_tracks) {
            if (matchedTracks.contains(id)) {
                continue;
            }
            const double distance = QLineF(center, state.track.box.center()).length();
            if (distance < bestDistance && distance <= config.retentionRadius) {
                bestDistance = distance;
                bestId = id;
            }
        }

        if (bestId == 0) {
            bestId = m_nextId++;
            State state;
            state.track.id = bestId;
            state.smoothedCenter = center;
            m_tracks.emplace(bestId, state);
        }

        auto& state = m_tracks.at(bestId);
        const QPointF previousCenter = state.smoothedCenter;
        const double smoothing = std::clamp(config.smoothing, 0.0, 0.95);
        state.smoothedCenter = QPointF(
            previousCenter.x() * smoothing + center.x() * (1.0 - smoothing),
            previousCenter.y() * smoothing + center.y() * (1.0 - smoothing));
        state.track.velocity = state.smoothedCenter - previousCenter;
        state.track.box = detection.box;
        state.track.box.moveCenter(state.smoothedCenter);
        state.track.confidence = detection.confidence;
        state.track.classId = detection.classId;
        state.track.label = detection.label;
        state.track.lostFrames = 0;
        state.track.selected = false;

        matchedTracks.insert(bestId);
    }

    std::vector<int> removeIds;
    for (auto& [id, state] : m_tracks) {
        if (!matchedTracks.contains(id)) {
            state.track.lostFrames += 1;
            state.track.box.translate(state.track.velocity);
        }
        if (state.track.lostFrames > config.maxLostFrames) {
            removeIds.push_back(id);
        }
    }

    for (const auto id : removeIds) {
        m_tracks.erase(id);
    }

    Tracks tracks;
    tracks.reserve(m_tracks.size());
    for (const auto& [id, state] : m_tracks) {
        Q_UNUSED(id);
        tracks.push_back(state.track);
    }
    std::sort(tracks.begin(), tracks.end(), [](const Track& left, const Track& right) {
        return left.id < right.id;
    });
    return tracks;
}

void CentroidTracker::reset() {
    m_tracks.clear();
    m_nextId = 1;
}

} // namespace vaporlock
