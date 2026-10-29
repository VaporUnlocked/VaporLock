#pragma once
#include <charconv>
#include <cmath>
#include <string>
#include <string_view>
#include <system_error>

// Collection-only typed token parsing. Assignment follows complete validation;
// unrelated targeting/live policies retain their existing behavior.
namespace vaporlock::collection {
inline bool integer(std::wstring_view token, int low, int high, int& target) {
    if (token.empty() || token.size() > 64 || low < 0 || high < low) return false;
    unsigned int parsed = 0;
    for (const wchar_t value : token) {
        if (value < L'0' || value > L'9') return false;
        const unsigned int digit = static_cast<unsigned int>(value - L'0');
        if (parsed > static_cast<unsigned int>(high) / 10
            || (parsed == static_cast<unsigned int>(high) / 10 && digit > static_cast<unsigned int>(high) % 10)) return false;
        parsed = parsed * 10 + digit;
    }
    if (parsed < static_cast<unsigned int>(low)) return false;
    target = static_cast<int>(parsed);
    return true;
}

inline bool confidence(std::wstring_view token, double& target) {
    if (token.empty() || token.size() > 64) return false;
    std::string ascii;
    ascii.reserve(token.size());
    bool digit = false, dot = false, exponent = false, exponentDigit = false;
    for (std::size_t i = 0; i < token.size(); ++i) {
        const wchar_t value = token[i];
        if (value >= L'0' && value <= L'9') {
            if (exponent) exponentDigit = true;
            else digit = true;
        } else if (value == L'.' && !dot && !exponent) {
            dot = true;
        } else if ((value == L'e' || value == L'E') && digit && !exponent) {
            exponent = true;
        } else if ((value == L'+' || value == L'-') && i > 0
            && (token[i - 1] == L'e' || token[i - 1] == L'E')) {
            // Exponent sign only: no leading whitespace, sign, hex or suffix.
        } else return false;
        ascii.push_back(static_cast<char>(value));
    }
    if (!digit || (exponent && !exponentDigit)) return false;
    double parsed = 0;
    const auto result = std::from_chars(ascii.data(), ascii.data() + ascii.size(), parsed, std::chars_format::general);
    if (result.ec != std::errc{} || result.ptr != ascii.data() + ascii.size()
        || !std::isfinite(parsed) || parsed < 0.01 || parsed > 1.0) return false;
    target = parsed;
    return true;
}
} // namespace vaporlock::collection
