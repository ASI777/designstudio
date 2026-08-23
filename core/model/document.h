// document.h — the mechatronic project document (format design-studio.project/3).
//
// One document spans mechanical solids and the electronic board (U2). It loads
// v3 files directly and MIGRATES older v1/v2 `.dsproj` projects forward by
// nesting the old project under `board` and adding the empty mechanical sections
// plus a rationale entry recording the migration. Saved atomically (temp+rename)
// like the C# ProjectIO, so a crash mid-write never corrupts the project.
#pragma once
#include "designcore/c_api.h"   // DcStatus codes
#include "model/mini_json.hpp"
#include <string>

namespace dc::model {

class Document {
public:
    static constexpr int kVersion = 3;

    // Load from disk, migrating v1/v2 forward in memory. Returns DC_OK or a
    // negative DcStatus (DC_ERR_IO / DC_ERR_PARSE / DC_ERR_MIGRATE).
    int load(const std::string& path);

    // Atomic write of the v3 document. Returns DC_OK or DC_ERR_IO.
    int save(const std::string& path) const;

    // Schema version of the in-memory document (3 once loaded/migrated).
    int version() const;

    const json::Json& root() const { return root_; }

private:
    json::Json root_ = json::Json::object();

    // Wrap a parsed v1/v2 project as the `board` of a fresh v3 document.
    static json::Json migrateToV3(const json::Json& old, int oldVersion);
};

}  // namespace dc::model
