-- Passive entity location/visibility tracker for density and navigation telemetry.
-- No combat log, movement commands, packet injection, or respawn inference.
local M, Tracker = {}, {}
Tracker.__index = Tracker
local MIN_MOVE, UPDATE_HEARTBEAT, LOOKUP_INTERVAL = 0.40, 5.0, 3.0
local DEATH_SIGNAL_AGE = 10.0

local function finite(v)
    v = tonumber(v)
    if not v or v ~= v or v == math.huge or v == -math.huge then return nil end
    return v
end
local function clean_name(s)
    if type(s) ~= 'string' then return nil end
    s = s:gsub('%z.*', ''):gsub('^%s+', ''):gsub('%s+$', '')
    return s ~= '' and s:sub(1, 96) or nil
end
local function hasbit(mask, bit)
    return mask and mask % (bit * 2) >= bit
end
local function kind_for(packet_type, windower_type)
    -- Windower mob.spawn_type is not the same enumeration as 0x05B Type.
    if windower_type == 16 then return 'monster', 'windower_spawn_type' end
    if windower_type == 14 then return 'non_monster', 'windower_spawn_type' end
    if packet_type == 3 then return 'monster', 'packet_spawn_type' end
    if packet_type == 10 then return 'self', 'packet_spawn_type' end
    -- Packet type 0 is ambiguous (casket or NPC), not an enemy classifier.
    if packet_type == 0 then return 'npc_or_casket', 'packet_spawn_type' end
    return 'unknown', 'unresolved'
end
local function packet_type(v)
    local n = finite(v)
    if n then return n end
    if type(v) == 'string' then
        local s = v:lower()
        if s == 'monster' then return 3 end
        if s == 'casket or npc' then return 0 end
        if s == 'self' then return 10 end
    end
end
local function percent(v)
    local n = finite(v)
    if not n and type(v) == 'string' then
        n = tonumber(v:match('^%s*(%d+%.?%d*)%%%s*$'))
    end
    if n and n >= 0 and n <= 100 then return n end
end
local function moved(old, x, y, z)
    if not old.x or not x or not y or not z then return false end
    local dx,dy,dz = x-old.x,y-old.y,z-old.z
    return dx*dx + dy*dy + dz*dz >= MIN_MOVE*MIN_MOVE
end
local function record(kind, id, index, source, e, at, position_is_packet)
    return {
        observation_kind=kind, source_packet=source,
        entity_id=id, entity_index=index, entity_epoch=e.epoch,
        entity_type=e.type, windower_spawn_type=e.windower_type,
        entity_kind=e.kind, entity_kind_source=e.kind_source,
        entity_name=e.name, entity_name_source=e.name_source,
        entity_x=e.x, entity_y=e.y, entity_z=e.z,
        position_source=position_is_packet and 'packet' or 'last_known',
        entity_position_age=e.position_at and math.max(0, at-e.position_at) or nil
    }
end
function M.new()
    return setmetatable({entities={}, epoch=0}, Tracker)
end
function Tracker:reset()
    local count=0
    for _ in pairs(self.entities) do count=count+1 end
    self.entities={}
    return count
end
function Tracker:observe(packet_id, p, zone, at, lookup)
    if type(p) ~= 'table' or not zone then return nil end
    local id = finite(p[packet_id == 0x00E and 'NPC' or 'ID'])
    local index = finite(p['Index'])
    if not id or id <= 0 or not index then return nil end
    local key = tostring(zone)..':'..tostring(id)..':'..tostring(index)
    local e = self.entities[key]
    -- Reposition packets alone do not establish an identity.
    if packet_id == 0x065 and not e then return nil end
    local mask = packet_id == 0x00E and finite(p['Mask']) or nil
    if packet_id == 0x00E and not mask then return nil end

    local hp_changed = packet_id == 0x00E and hasbit(mask, 4)
    if e and hp_changed then
        -- Keep only the most recent death-related context in memory.
        local hp = percent(p['HP %'])
        if hp ~= nil then e.last_hp, e.last_hp_at = hp, at end
        if type(p['Status']) == 'string' or type(p['Status']) == 'number' then
            e.last_status = tostring(p['Status'])
            e.last_status_at = at
        end
    end

    if packet_id == 0x00E and hasbit(mask, 32) then
        if not e then return nil end
        self.entities[key] = nil
        local result = record('last_seen',id,index,packet_id,e,at,false)
        result.visibility_end_reason='npc_update_visibility_bit'
        if e.last_hp ~= nil then result.last_hp_percent=e.last_hp end
        if e.last_status ~= nil then result.last_entity_status=e.last_status end
        if e.last_hp_at then result.last_hp_age=math.max(0,at-e.last_hp_at) end
        if e.last_hp == 0 and e.last_hp_at and at-e.last_hp_at <= DEATH_SIGNAL_AGE then
            result.death_signal='recent_zero_hp'
        elseif e.last_status and e.last_status:lower():find('dead',1,true) and
            e.last_status_at and at-e.last_status_at <= DEATH_SIGNAL_AGE then
            result.death_signal='recent_dead_status'
        end
        -- Absence of a death signal does not imply range exit.
        result.disappearance_cause='unknown'
        return result
    end

    local position_present = packet_id ~= 0x00E or hasbit(mask, 1)
    local x=position_present and finite(p['X']) or nil
    local y=position_present and finite(p['Y']) or nil
    local z=position_present and finite(p['Z']) or nil
    if x == nil or y == nil or z == nil then x,y,z=nil,nil,nil end
    if not e and x == nil then return nil end

    local typ = packet_id == 0x05B and packet_type(p['Type']) or (e and e.type)
    local name = e and e.name or nil
    local name_source = e and e.name_source or nil
    if packet_id == 0x00E and hasbit(mask, 8) and name_source ~= 'windower_mob' then
        -- NPC update names can be truncated or omit spaces. Once a verified
        -- Windower cache name is available, never downgrade to packet text.
        local packet_name = clean_name(p['Name'])
        if packet_name then name, name_source = packet_name, 'packet_update' end
    end

    local wt = e and e.windower_type or nil
    local kind = kind_for(typ,wt)
    -- A packet name is provisional even if the mob type is already known.
    -- Retry the bounded cache lookup until the canonical name is obtained.
    local unresolved = name_source ~= 'windower_mob' or kind == 'unknown' or
        (kind == 'npc_or_casket' and wt == nil)
    if type(lookup) == 'function' and unresolved and
       (not e or not e.last_lookup or at - e.last_lookup >= LOOKUP_INTERVAL) then
        local ok, mob = pcall(lookup,id)
        if e then e.last_lookup=at end
        if ok and type(mob)=='table' and
           (mob.id == nil or tonumber(mob.id)==id) and
           (mob.index == nil or tonumber(mob.index)==index) then
            local cache_name = clean_name(mob.name)
            if cache_name then name, name_source = cache_name, 'windower_mob' end
            wt = finite(mob.spawn_type) or wt
        end
    end
    local resolved, resolved_source = kind_for(typ,wt)
    if not e then
        self.epoch=self.epoch+1
        e={epoch=self.epoch,type=typ,windower_type=wt,kind=resolved,
            kind_source=resolved_source,name=name,name_source=name_source,
            x=x,y=y,z=z,position_at=at,
            last_emitted=at,last_lookup=at}
        self.entities[key]=e
        return record('first_seen',id,index,packet_id,e,at,true)
    end

    local changed_position=moved(e,x,y,z)
    local changed_identity=(e.name~=name or e.name_source~=name_source or
        e.kind~=resolved or e.type~=typ or e.windower_type~=wt)
    if x then
        e.x,e.y,e.z=x,y,z
        e.position_at=at
    end
    e.type,e.windower_type,e.kind,e.kind_source,e.name,e.name_source =
        typ,wt,resolved,resolved_source,name,name_source

    local kind_of_event
    if changed_position then kind_of_event='position_update'
    elseif changed_identity then kind_of_event='identity_update'
    elseif at-e.last_emitted >= UPDATE_HEARTBEAT then kind_of_event='still_visible'
    else return nil end
    e.last_emitted=at
    return record(kind_of_event,id,index,packet_id,e,at,x~=nil)
end
return M
