// mini_json.hpp — a small, dependency-free JSON value + parser + serializer.
//
// The C++ core has no JSON library and the build is offline, so this provides
// just enough JSON for the mechatronic document loader (U2): the six value
// kinds, order-preserving objects (stable diffs / git-friendly output), basic
// string escapes, and pretty printing. It is not a spec-complete parser — it
// targets the documents this app writes — but it round-trips them faithfully.
#pragma once
#include <cstdint>
#include <cmath>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace dc::json {

class Json {
public:
    enum class Type { Null, Bool, Number, String, Array, Object };

    Json() : t_(Type::Null) {}
    Json(bool b) : t_(Type::Bool), bool_(b) {}
    Json(double n) : t_(Type::Number), num_(n) {}
    Json(int n) : t_(Type::Number), num_(double(n)) {}
    Json(std::int64_t n) : t_(Type::Number), num_(double(n)) {}
    Json(const char* s) : t_(Type::String), str_(s) {}
    Json(std::string s) : t_(Type::String), str_(std::move(s)) {}

    static Json array() { Json j; j.t_ = Type::Array; return j; }
    static Json object() { Json j; j.t_ = Type::Object; return j; }

    Type type() const { return t_; }
    bool isObject() const { return t_ == Type::Object; }
    bool isArray() const { return t_ == Type::Array; }
    bool isNull() const { return t_ == Type::Null; }

    bool asBool() const { return bool_; }
    double asNumber() const { return num_; }
    std::int64_t asInt() const { return std::int64_t(std::llround(num_)); }
    const std::string& asString() const { return str_; }
    const std::vector<Json>& items() const { return arr_; }
    const std::vector<std::pair<std::string, Json>>& members() const { return obj_; }

    void push_back(Json v) { arr_.push_back(std::move(v)); }   // array

    // object access: get (null if absent) / set (insert or replace, ordered)
    const Json* find(const std::string& key) const {
        for (auto& kv : obj_) if (kv.first == key) return &kv.second;
        return nullptr;
    }
    Json& set(const std::string& key, Json v) {
        for (auto& kv : obj_) if (kv.first == key) { kv.second = std::move(v); return kv.second; }
        obj_.emplace_back(key, std::move(v));
        return obj_.back().second;
    }

    std::string dump(int indent = 2) const { std::string o; write(o, indent, 0); return o; }

private:
    Type t_;
    bool bool_ = false;
    double num_ = 0.0;
    std::string str_;
    std::vector<Json> arr_;
    std::vector<std::pair<std::string, Json>> obj_;

    static void escape(std::string& o, const std::string& s) {
        o += '"';
        for (char c : s) {
            switch (c) {
                case '"': o += "\\\""; break;
                case '\\': o += "\\\\"; break;
                case '\n': o += "\\n"; break;
                case '\r': o += "\\r"; break;
                case '\t': o += "\\t"; break;
                default: o += c;
            }
        }
        o += '"';
    }
    static void pad(std::string& o, int indent, int depth) {
        if (indent > 0) { o += '\n'; o.append(std::size_t(indent) * depth, ' '); }
    }
    void write(std::string& o, int indent, int depth) const {
        switch (t_) {
            case Type::Null: o += "null"; break;
            case Type::Bool: o += bool_ ? "true" : "false"; break;
            case Type::Number: {
                if (num_ == std::floor(num_) && std::abs(num_) < 1e15) {
                    o += std::to_string(std::int64_t(num_));
                } else {
                    char buf[32]; std::snprintf(buf, sizeof buf, "%.10g", num_); o += buf;
                }
                break;
            }
            case Type::String: escape(o, str_); break;
            case Type::Array: {
                if (arr_.empty()) { o += "[]"; break; }
                o += '[';
                for (std::size_t i = 0; i < arr_.size(); ++i) {
                    if (i) o += ',';
                    pad(o, indent, depth + 1);
                    arr_[i].write(o, indent, depth + 1);
                }
                pad(o, indent, depth); o += ']';
                break;
            }
            case Type::Object: {
                if (obj_.empty()) { o += "{}"; break; }
                o += '{';
                for (std::size_t i = 0; i < obj_.size(); ++i) {
                    if (i) o += ',';
                    pad(o, indent, depth + 1);
                    escape(o, obj_[i].first);
                    o += indent > 0 ? ": " : ":";
                    obj_[i].second.write(o, indent, depth + 1);
                }
                pad(o, indent, depth); o += '}';
                break;
            }
        }
    }
};

// ---- parser (recursive descent over a NUL-terminated buffer) ----
class Parser {
public:
    // Returns true on success; on failure `error` describes where.
    static bool parse(const std::string& src, Json& out, std::string& error) {
        Parser p(src.c_str());
        p.ws();
        bool ok = p.value(out);
        if (ok) { p.ws(); if (*p.s_ != '\0') { ok = false; p.err_ = "trailing data"; } }
        if (!ok) error = p.err_;
        return ok;
    }

private:
    explicit Parser(const char* s) : s_(s) {}
    const char* s_;
    std::string err_;

    void ws() { while (*s_ == ' ' || *s_ == '\t' || *s_ == '\n' || *s_ == '\r') ++s_; }

    bool value(Json& out) {
        ws();
        switch (*s_) {
            case '{': return object(out);
            case '[': return array(out);
            case '"': { std::string s; if (!str(s)) return false; out = Json(std::move(s)); return true; }
            case 't': return lit("true", Json(true), out);
            case 'f': return lit("false", Json(false), out);
            case 'n': return lit("null", Json(), out);
            default: return number(out);
        }
    }
    bool lit(const char* word, Json v, Json& out) {
        for (const char* w = word; *w; ++w, ++s_) if (*s_ != *w) { err_ = "bad literal"; return false; }
        out = std::move(v); return true;
    }
    bool number(Json& out) {
        const char* start = s_;
        if (*s_ == '-') ++s_;
        while (*s_ >= '0' && *s_ <= '9') ++s_;
        if (*s_ == '.') { ++s_; while (*s_ >= '0' && *s_ <= '9') ++s_; }
        if (*s_ == 'e' || *s_ == 'E') { ++s_; if (*s_ == '+' || *s_ == '-') ++s_; while (*s_ >= '0' && *s_ <= '9') ++s_; }
        if (s_ == start) { err_ = "bad number"; return false; }
        out = Json(std::strtod(start, nullptr));
        return true;
    }
    bool str(std::string& out) {
        ++s_;  // opening quote
        while (*s_ && *s_ != '"') {
            if (*s_ == '\\') {
                ++s_;
                switch (*s_) {
                    case '"': out += '"'; break;   case '\\': out += '\\'; break;
                    case '/': out += '/'; break;    case 'n': out += '\n'; break;
                    case 't': out += '\t'; break;   case 'r': out += '\r'; break;
                    case 'b': out += '\b'; break;   case 'f': out += '\f'; break;
                    case 'u': {                      // \uXXXX -> minimal: emit '?' for non-ASCII
                        unsigned code = 0;
                        for (int i = 0; i < 4 && s_[1]; ++i) {
                            ++s_; char c = *s_; code <<= 4;
                            if (c >= '0' && c <= '9') code += unsigned(c - '0');
                            else if (c >= 'a' && c <= 'f') code += unsigned(c - 'a' + 10);
                            else if (c >= 'A' && c <= 'F') code += unsigned(c - 'A' + 10);
                        }
                        if (code < 0x80) out += char(code); else out += '?';
                        break;
                    }
                    default: out += *s_;
                }
                ++s_;
            } else { out += *s_++; }
        }
        if (*s_ != '"') { err_ = "unterminated string"; return false; }
        ++s_;
        return true;
    }
    bool array(Json& out) {
        out = Json::array();
        ++s_; ws();
        if (*s_ == ']') { ++s_; return true; }
        while (true) {
            Json v; if (!value(v)) return false;
            out.push_back(std::move(v));
            ws();
            if (*s_ == ',') { ++s_; continue; }
            if (*s_ == ']') { ++s_; return true; }
            err_ = "expected , or ]"; return false;
        }
    }
    bool object(Json& out) {
        out = Json::object();
        ++s_; ws();
        if (*s_ == '}') { ++s_; return true; }
        while (true) {
            ws();
            if (*s_ != '"') { err_ = "expected key"; return false; }
            std::string key; if (!str(key)) return false;
            ws(); if (*s_ != ':') { err_ = "expected :"; return false; } ++s_;
            Json v; if (!value(v)) return false;
            out.set(key, std::move(v));
            ws();
            if (*s_ == ',') { ++s_; continue; }
            if (*s_ == '}') { ++s_; return true; }
            err_ = "expected , or }"; return false;
        }
    }
};

}  // namespace dc::json
