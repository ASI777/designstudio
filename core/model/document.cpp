// document.cpp — Document load/save/migrate + the dc_doc_* C-API (U2).
// Defining DC_H0_HAVE_DOC (via CMake) excludes the matching stubs in
// c_api_h0_stubs.cpp so these become the real boundary implementation.
#include "model/document.h"

#include <cstdio>
#include <ctime>
#include <fstream>
#include <sstream>
#include <string>

namespace dc::model {

using json::Json;

static std::string nowIso8601() {
    // Coarse UTC timestamp without pulling in <chrono> formatting; good enough
    // for a rationale audit entry.
    std::time_t t = std::time(nullptr);
    char buf[32];
    std::strftime(buf, sizeof buf, "%Y-%m-%dT%H:%M:%SZ", std::gmtime(&t));
    return buf;
}

Json Document::migrateToV3(const Json& old, int oldVersion) {
    Json doc = Json::object();
    doc.set("format", Json("design-studio.project/3"));
    doc.set("units", Json("nm"));
    doc.set("materials", Json::array());
    doc.set("parts", Json::array());
    doc.set("board", old);                       // the entire old project, preserved
    Json assembly = Json::object();
    assembly.set("tree", Json::array());
    assembly.set("joints", Json::array());
    doc.set("assembly", assembly);
    doc.set("constraints", Json::array());
    doc.set("tolerances", Json::array());

    Json rationale = Json::array();
    Json note = Json::object();
    note.set("ts", Json(nowIso8601()));
    note.set("intent", Json("Migrated from project v" + std::to_string(oldVersion) +
                            " — electronic board nested under `board`; mechanical "
                            "sections initialised empty."));
    note.set("gate", Json("none"));
    note.set("source", Json("human"));
    rationale.push_back(std::move(note));
    doc.set("rationale", std::move(rationale));
    return doc;
}

int Document::load(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) return DC_ERR_IO;
    std::stringstream ss;
    ss << f.rdbuf();
    const std::string text = ss.str();

    Json parsed;
    std::string err;
    if (!json::Parser::parse(text, parsed, err)) return DC_ERR_PARSE;
    if (!parsed.isObject()) return DC_ERR_PARSE;

    // Already v3?
    if (const Json* fmt = parsed.find("format");
        fmt && fmt->type() == Json::Type::String &&
        fmt->asString() == "design-studio.project/3") {
        root_ = parsed;
        return DC_OK;
    }

    // Otherwise treat as a legacy project and migrate. `version` is the v1/v2
    // marker; absent → assume v1.
    int oldVersion = 1;
    if (const Json* v = parsed.find("version");
        v && v->type() == Json::Type::Number) {
        oldVersion = int(v->asInt());
    }
    root_ = migrateToV3(parsed, oldVersion);
    return DC_OK;
}

int Document::save(const std::string& path) const {
    const std::string tmp = path + ".tmp";
    {
        std::ofstream f(tmp, std::ios::binary | std::ios::trunc);
        if (!f) return DC_ERR_IO;
        f << root_.dump(2) << '\n';
        if (!f) return DC_ERR_IO;
    }
    std::remove(path.c_str());                   // Windows rename won't overwrite
    if (std::rename(tmp.c_str(), path.c_str()) != 0) return DC_ERR_IO;
    return DC_OK;
}

int Document::version() const {
    if (const Json* fmt = root_.find("format");
        fmt && fmt->type() == Json::Type::String &&
        fmt->asString() == "design-studio.project/3") {
        return kVersion;
    }
    if (const Json* v = root_.find("version"); v && v->type() == Json::Type::Number)
        return int(v->asInt());
    return DC_ERR_PARSE;
}

}  // namespace dc::model

// ---- C-API boundary (real impl; excludes the c_api_h0_stubs.cpp doc stubs) ---
DC_API std::int32_t dc_doc_open(const char* path, DcDocHandle* out) {
    if (!path || !out) return DC_ERR_INVALID_ARG;
    auto* doc = new dc::model::Document();
    int rc = doc->load(path);
    if (rc != DC_OK) { delete doc; *out = nullptr; return rc; }
    *out = doc;
    return DC_OK;
}

DC_API std::int32_t dc_doc_save(DcDocHandle h, const char* path) {
    if (!h || !path) return DC_ERR_INVALID_ARG;
    return static_cast<dc::model::Document*>(h)->save(path);
}

DC_API std::int32_t dc_doc_version(DcDocHandle h) {
    if (!h) return DC_ERR_INVALID_HANDLE;
    return static_cast<dc::model::Document*>(h)->version();
}

DC_API void dc_doc_destroy(DcDocHandle h) {
    delete static_cast<dc::model::Document*>(h);
}
