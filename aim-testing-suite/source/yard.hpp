#pragma once

#include "range.hpp"
#include <array>
#include <limits>

namespace suite {

enum class YardMode { Clear, Hold, Practice };
enum class YardPhase { Ready, Active, Intermission, Won, Lost };
enum class WeaponClass { Rifle, Smg, Burst, Marksman, Sniper, Shotgun, Lmg, Pistol };

struct YardWeapon {
    const char* name;
    const char* role;
    const char* description;
    WeaponClass type;
    int magazine, reserve, damage, burst, pellets;
    float interval, reload, recoil, spread, aimedSpread, zoom, reach, soundPitch;
    bool automatic;
    Color accent;
};

inline constexpr std::array<YardWeapon,8> YardWeapons{{
    {"HARBOR AR-4", "ASSAULT RIFLE", "Balanced automatic rifle for the container lanes.", WeaponClass::Rifle, 30,120,35,1,1,.105f,1.65f,.58f,.95f,.15f,.74f,58,1.05f,true,{245,174,95,255}},
    {"VECTOR-9", "SUBMACHINE GUN", "Fast handling and high fire rate for tight interiors.", WeaponClass::Smg, 34,136,24,1,1,.070f,1.25f,.40f,1.4f,.32f,.83f,28,1.35f,true,{116,223,197,255}},
    {"RELAY B3", "BURST RIFLE", "Three quick shots per trigger pull with a recovery pause.", WeaponClass::Burst, 27,108,37,3,1,.063f,1.78f,.51f,1.05f,.13f,.69f,62,1.16f,false,{151,207,255,255}},
    {"LONGWATCH DMR", "MARKSMAN RIFLE", "Hard-hitting semi-auto with a magnified optic.", WeaponClass::Marksman, 12,60,69,1,1,.255f,1.8f,1.10f,1.35f,.035f,.55f,88,.81f,false,{218,191,122,255}},
    {"CRANE .308", "BOLT-ACTION", "One precise shot with a deliberate bolt cycle.", WeaponClass::Sniper, 5,25,112,1,1,1.08f,2.35f,2.1f,2.2f,.015f,.36f,130,.64f,false,{216,181,248,255}},
    {"BREACH 12", "PUMP SHOTGUN", "Eight pellets per shell with a short effective range.", WeaponClass::Shotgun, 6,36,19,1,8,.72f,2.05f,2.4f,5.4f,3.4f,.82f,16,.72f,false,{244,130,117,255}},
    {"FOUNDRY LMG", "LIGHT MACHINE GUN", "A large belt and heavy recoil. Slow to reload.", WeaponClass::Lmg, 70,210,40,1,1,.126f,3.40f,.84f,1.6f,.21f,.67f,72,.86f,true,{210,223,133,255}},
    {"KITE .45", "SIDEARM", "A crisp semi-auto with the fastest reload.", WeaponClass::Pistol, 11,55,46,1,1,.195f,.92f,.84f,1.25f,.27f,.87f,30,1.47f,false,{233,218,183,255}}
}};

inline constexpr const char* yardModeName(YardMode mode) {
    switch (mode) {
    case YardMode::Clear: return "Clear the Yard";
    case YardMode::Hold: return "Hold the Line";
    case YardMode::Practice: return "Free Practice";
    }
    return "";
}

struct YardBox { BoundingBox bounds; Color color; };
inline constexpr std::array<YardBox,22> YardStructures{{
    {{{-24,0,-14},{-23,5,46}}, {65,76,86,255}},
    {{{23,0,-14},{24,5,46}}, {65,76,86,255}},
    {{{-24,0,-15},{24,5,-14}}, {77,80,79,255}},
    {{{-24,0,44},{24,8,46}}, {62,75,86,255}},
    {{{-17,0,7},{-10,2.9f,12}}, {102,67,46,255}},
    {{{-18,0,19},{-11,3.1f,24}}, {55,83,83,255}},
    {{{-8,0,13},{-4,1.14f,14.1f}}, {81,87,86,255}},
    {{{-1.7f,0,18},{2,1.15f,19}}, {86,92,92,255}},
    {{{7,0,14},{11,2.85f,18}}, {106,68,42,255}},
    {{{2,0,29},{7,2.85f,34}}, {58,89,90,255}},
    {{{-9,0,34},{-4,1.2f,35}}, {79,87,90,255}},
    {{{-20,0,29},{-19.4f,5,41}}, {69,78,90,255}},
    {{{-19.4f,0,40.5f},{-10.5f,5,41}}, {69,78,90,255}},
    {{{-11,0,36},{-10.5f,5,41}}, {69,78,90,255}},
    {{{-11,0,29},{-10.5f,5,32.5f}}, {69,78,90,255}},
    {{{-20,4.55f,29},{-10.5f,5,41}}, {56,65,76,255}},
    {{{14,0,25},{22,1.9f,28}}, {82,83,76,255}},
    {{{14,0,39},{22,1.85f,42}}, {82,83,76,255}},
    {{{14,2.2f,26},{22,2.6f,41}}, {70,81,89,255}},
    {{{14,2.6f,30},{14.2f,3.1f,37}}, {99,104,100,255}},
    {{{-5.0f,0,-1.4f},{-1.8f,1.1f,-.5f}}, {84,91,91,255}},
    {{{2.2f,0,1.5f},{5.4f,1.1f,2.35f}}, {84,91,91,255}}
}};

inline const std::vector<BoundingBox>& yardSolids() {
    static const auto boxes = [] {
        std::vector<BoundingBox> result;
        for (const auto& structure : YardStructures) result.push_back(structure.bounds);
        return result;
    }();
    return boxes;
}

inline float yardWallDistance(Ray ray, float maximum) {
    float nearest = maximum;
    for (const auto& bounds : yardSolids()) {
        float distance = 0;
        if (rayBox(ray.position, ray.direction, bounds, distance)) nearest = std::min(nearest, distance);
    }
    if (ray.direction.y < -.00001f)
        nearest = std::min(nearest, std::max(0.f, -ray.position.y / ray.direction.y));
    return nearest;
}

struct YardRoute { Vector3 start, end; float delay; bool crouch; };
inline constexpr std::array<YardRoute,10> YardRoutes{{
    {{-3,0,9},{3.2f,0,11},0,false},
    {{-9,0,15},{-3.1f,0,15.5f},.5f,true},
    {{13,0,13},{18,0,17},1,false},
    {{3,0,22},{10,0,23},1.5f,false},
    {{-8.8f,0,26},{-2,0,26},1.7f,true},
    {{-16,0,36},{-11.7f,0,34},2.2f,false},
    {{-6,0,38},{1,0,39},2.8f,false},
    {{19,2.6f,31},{20.8f,2.6f,37},3.2f,true},
    {{10,0,40},{12.5f,0,33},3.8f,false},
    {{-.5f,0,43},{10.5f,0,42.5f},4.0f,false}
}};

struct YardBot {
    Vector3 position{};
    float facing = 180, height = 1.82f;
    float health = 100, reappear = 0, shootAt = 0, warnedAt = 0;
    float flashUntil = 0, fireUntil = 0;
    int attacks = 0;
    bool active = false, moving = false, exposed = false, cleared = false;
};

struct YardReplayEvent {
    float time;
    Vector3 start, end;
    int target;
    bool hit, enemy;
};

struct YardRound {
    YardMode mode = YardMode::Clear;
    YardPhase phase = YardPhase::Ready;
    int weaponIndex = 0, seed = 407, wave = 1, kills = 0, shots = 0, hits = 0, headshots = 0;
    int ammo = 30, reserve = 120, burstLeft = 0;
    float health = 100, time = 0, ready = 1.2f, cooldown = 0, reloadLeft = 0;
    float shotAt = -10, impactAt = -10, hurtAt = -10, ads = 0, slide = 0, slideReady = 0;
    double accumulator = 0;
    Vector3 slideDirection{};
    std::array<YardBot,YardRoutes.size()> bots{};
    std::vector<YardReplayEvent> events;

    const YardWeapon& gun() const { return YardWeapons[weaponIndex]; }
    int population() const { return mode == YardMode::Hold ? 3 + wave : static_cast<int>(YardRoutes.size()); }
    bool finished() const { return phase == YardPhase::Won || phase == YardPhase::Lost; }
    bool inCombat() const { return phase == YardPhase::Active; }
    float accuracy() const { return shots ? 100.f * hits / shots : 0.f; }

    void reset(YardMode selected, int weapon, int roundSeed) {
        *this = {};
        mode = selected;
        seed = roundSeed;
        weaponIndex = std::clamp(weapon, 0, static_cast<int>(YardWeapons.size()) - 1);
        replenish();
        spawnWave();
    }

    void replenish() {
        ammo = gun().magazine;
        reserve = gun().reserve;
        reloadLeft = cooldown = 0;
        burstLeft = 0;
    }

    void spawnWave() {
        bots = {};
        for (int i = 0; i < population(); ++i) {
            auto& bot = bots[i];
            bot.position = route(i).start;
            bot.active = true;
            bot.health = 100;
            bot.reappear = time + (mode == YardMode::Hold ? i * .48f : route(i).delay);
            bot.shootAt = bot.reappear + 1.4f + unitHash(seed,i,wave) * 1.6f;
        }
    }

    const YardRoute& route(int index) const {
        const int offset = mode == YardMode::Hold ? (wave - 1) * 2 : 0;
        return YardRoutes[(index + offset) % YardRoutes.size()];
    }

    void reload() {
        if (inCombat() && !reloadLeft && ammo < gun().magazine && reserve > 0) {
            reloadLeft = gun().reload;
            burstLeft = 0;
        }
    }

    bool trigger(bool pressed, bool held) {
        if (!inCombat() || reloadLeft > 0 || cooldown > 0) return false;
        if (!burstLeft && !(gun().automatic ? pressed || held : pressed)) return false;
        if (!ammo) {
            reload();
            return false;
        }
        if (!burstLeft) burstLeft = gun().burst;
        --burstLeft;
        --ammo;
        ++shots;
        shotAt = time;
        cooldown = gun().interval + (gun().burst > 1 && !burstLeft ? .24f : 0.f);
        return true;
    }

    void record(YardReplayEvent event) {
        if (events.size() < 12000) events.push_back(event);
    }

    void damage(int id, float amount, bool head) {
        auto& bot = bots[id];
        if (!bot.active || time < bot.reappear) return;
        bot.health -= amount;
        bot.flashUntil = time + .14f;
        impactAt = time;
        if (bot.health > 0) return;
        bot.active = false;
        bot.cleared = true;
        ++kills;
        if (head) ++headshots;
        if (mode == YardMode::Practice) {
            bot.cleared = false;
            bot.reappear = time + .85f;
        }
    }

    void advance(float dt, Vector3 player) {
        if (!std::isfinite(dt) || dt <= 0) return;
        accumulator += std::clamp(dt, 0.f, .15f);
        while (accumulator >= TickSeconds) {
            accumulator -= TickSeconds;
            tick(float(TickSeconds), player);
        }
    }

    void tick(float dt, Vector3 player) {
        if (finished()) return;
        if (phase == YardPhase::Ready || phase == YardPhase::Intermission) {
            ready = std::max(0.f, ready - dt);
            if (ready > 0) return;
            if (phase == YardPhase::Intermission) spawnWave();
            phase = YardPhase::Active;
        }
        time += dt;
        cooldown = std::max(0.f, cooldown - dt);
        if (reloadLeft > 0) {
            reloadLeft = std::max(0.f, reloadLeft - dt);
            if (!reloadLeft) {
                const int taken = std::min(gun().magazine - ammo, reserve);
                ammo += taken;
                reserve -= taken;
                if (mode == YardMode::Practice) reserve = gun().reserve;
            }
        }
        int remaining = 0;
        for (int i = 0; i < population(); ++i) {
            auto& bot = bots[i];
            if (bot.cleared) continue;
            ++remaining;
            if (time < bot.reappear) continue;
            if (!bot.active) {
                bot.active = true;
                bot.health = 100;
            }
            updateBot(i, dt, player);
        }
        if (mode == YardMode::Practice) return;
        if (health <= 0 || (mode == YardMode::Clear && time >= 90.f && remaining)) phase = YardPhase::Lost;
        else if (!remaining) {
            if (mode == YardMode::Clear || wave == 3) phase = YardPhase::Won;
            else {
                ++wave;
                phase = YardPhase::Intermission;
                ready = 2.5f;
                health = std::min(100.f, health + 32);
                replenish();
            }
        }
    }

    void updateBot(int id, float dt, Vector3 player) {
        auto& bot = bots[id];
        const auto& patrol = route(id);
        const float frequency = mode == YardMode::Hold ? .95f : .67f;
        const float offset = unitHash(seed,id,wave) * 6.28f;
        const float walk = std::sin((time - bot.reappear) * frequency + offset);
        Vector3 goal = Vector3Lerp(patrol.start, patrol.end, .5f + .5f * walk);
        if (mode == YardMode::Hold && goal.y == 0) {
            // Three push lanes keep reinforcements around solid cover.
            const float lane = id % 3 == 0 ? -20.8f : id % 3 == 1 ? -2.8f : 12.6f;
            const float approach = std::clamp((time - bot.reappear - .7f) * .15f, 0.f, .91f);
            const Vector3 rally{lane,0,goal.z};
            goal = approach < .24f ? Vector3Lerp(goal,rally,approach/.24f)
                                    : Vector3Lerp(rally,{lane,0,5.f+id%2*2},(approach-.24f)/.67f);
        }
        auto delta = Vector3Subtract(goal,bot.position);
        const float distance = Vector3Length(delta);
        auto before = bot.position;
        if (distance > .02f) {
            delta = Vector3Scale(delta,std::min(distance,(mode==YardMode::Hold?4.1f:2.9f)*dt)/distance);
            if (bot.position.y > 1) bot.position = Vector3Add(bot.position,delta);
            else bot.position = movePlayer(bot.position,delta,yardSolids());
        }
        bot.moving = Vector3LengthSqr(Vector3Subtract(before,bot.position)) > .00007f;
        bot.height = patrol.crouch && walk < .15f ? 1.43f : 1.82f;
        const auto toPlayer = Vector3Subtract(player,bot.position);
        bot.facing = std::atan2(toPlayer.x,toPlayer.z)*RAD2DEG;
        const Vector3 eye = Vector3Add(bot.position,{0,bot.height*.84f,0});
        bot.exposed = Vector3Distance(eye,player)<42 && !covered(eye,player,yardSolids());
        if (mode == YardMode::Practice || !bot.exposed) {
            bot.warnedAt = 0;
            return;
        }
        if (!bot.warnedAt) bot.warnedAt = time;
        if (time < bot.shootAt || time - bot.warnedAt < .63f) return;
        const float roll = unitHash(seed, id, 17*wave + ++bot.attacks);
        bot.shootAt = time + (mode == YardMode::Hold ? 1.65f : 2.15f) + roll * 1.35f;
        bot.fireUntil = time + .10f;
        const bool hit = roll > .44f + std::min(.25f,Vector3Distance(eye,player)*.004f);
        if (hit) {
            health = std::max(0.f, health - (mode == YardMode::Hold ? 11.f : 12.f));
            hurtAt = time;
        }
        record({time, eye, player, id, hit, true});
    }
};

} // namespace suite
