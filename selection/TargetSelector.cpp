#include "selection/TargetSelector.h"

#include <QLineF>

#include <algorithm>
#include <cmath>
#include <limits>

namespace vaporlock {

namespace {

double areaOf(const QRectF& box) {
    return box.width() * box.height();
}

double speedOf(const Track& track) {
    return std::hypot(track.velocity.x(), track.velocity.y());
}

double scoreTrack(const Track& track, const QPointF& center, SelectionStrategy strategy) {
    switch (strategy) {
    case SelectionStrategy::HighestConfidence:
        return -track.confidence;
    case SelectionStrategy::LargestObject:
        return -areaOf(track.box);
    case SelectionStrategy::SmallestObject:
        return areaOf(track.box);
    case SelectionStrategy::LowestMovement:
        return speedOf(track);
    case SelectionStrategy::ClosestToCenter:
    default:
        return QLineF(track.box.center(), center).length();
    }
}

} // namespace

SelectedTrack TargetSelector::select(const Tracks& tracks, const QSize& frameSize, const AppConfig& config) {
    if (tracks.empty() || !frameSize.isValid()) {
        m_retainedId = 0;
        return std::nullopt;
    }

    const QPointF center(frameSize.width() / 2.0, frameSize.height() / 2.0);
    const Track* retained = nullptr;
    for (const auto& track : tracks) {
        if (track.id == m_retainedId && QLineF(track.box.center(), center).length() <= config.retentionRadius) {
            retained = &track;
            break;
        }
    }

    const Track* best = nullptr;
    double bestScore = std::numeric_limits<double>::max();
    for (const auto& track : tracks) {
        const auto distanceFromCenter = QLineF(track.box.center(), center).length();
        if (distanceFromCenter > config.acquisitionRadius && track.id != m_retainedId) {
            continue;
        }
        const double score = scoreTrack(track, center, config.selectionStrategy);
        if (score < bestScore) {
            bestScore = score;
            best = &track;
        }
    }

    if (retained != nullptr && best != nullptr) {
        const double retainedScore = scoreTrack(*retained, center, config.selectionStrategy);
        const double improvement = retainedScore == 0.0 ? retainedScore - bestScore : (retainedScore - bestScore) / std::abs(retainedScore);
        if (best->id != retained->id && improvement < config.switchingHysteresis) {
            best = retained;
        }
    }

    if (best == nullptr) {
        m_retainedId = 0;
        return std::nullopt;
    }

    Track selected = *best;
    selected.selected = true;
    m_retainedId = selected.id;
    return selected;
}

void TargetSelector::reset() {
    m_retainedId = 0;
}

} // namespace vaporlock
