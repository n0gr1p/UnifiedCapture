-- Read-only entity observation state; no packet injection or movement control.
-- 0x05B = spawn/appearance, 0x00E = NPC update, 0x065 = repositioning.
local M = {}
local Tracker = {}
Tracker.__index = Tracker

local MIN_MOVE = 0.40
local UPDATE_HEARTBEAT = 5.0

local function finite(v)
    v = tonumber(v)
    if not v or v ~= v or v == math.huge or v == -math.huge then return nil end
    return v
end

local function clean_name(s)
    if type(s) ~= 'string' then return nil end
    s = s:gsub('%z.*', ''):gsub('^%s+', ''):gsub('%s+$', '')
    if s == '' then return nil end
    return s:sub(1, 96)
end

local function hasbit(mask, bit)
    return (mask % (bit * 2)) >= bit
end

local function kind_for(t)
    if t == 3 then return 'monster' end
    if t == 0 then return 'npc_or_casket' end
    if t == 10 then return 'self' end
    return 'unknown'
end

local function moved(a, x, y, z)
    if not a or not a.x or not x or not y or not z then return false end
    local dx, dy, dz = x-a.x, y-a.y, z-a.z
    return dx*dx + dy*dy + dz*dz >= MIN_MOVE*MIN_MOVE
end

function M.new()
    return setmetatable({entities={}, epoch=0}, Tracker)
end

function Tracker:reset()
    local count = 0
    for _ in pairs(self.entities) do count = count + 1 end
    self.entities = {}
    return count
end

function Tracker:observe(packet_id, parsed, zone, now, lookup)
    if type(parsed) ~= 'table' or not zone then return nil end
    local id = finite(parsed[packet_id == 0x00E and 'NPC' or 'ID'])
    local index = finite(parsed['Index'])
    if not id or id <= 0 or not index then return nil end
    local key = tostring(zone) .. ':' .. tostring(id) .. ':' .. tostring(index)
    local old = self.entities[key]

    -- 0x065 may be the player's own repositioning: do not create an
    -- untyped entity from it. It can update an already observed identity.
    if packet_id == 0x065 and not old then return nil end

    local mask = packet_id == 0x00E and finite(parsed['Mask']) or nil
    if packet_id == 0x00E and not mask then return nil end
    local disappearing = mask and hasbit(mask, 32)
    if disappearing then
        if not old then return nil end
        self.entities[key] = nil
        return {
            observation_kind='last_seen', source_packet=packet_id,
            entity_id=id, entity_index=index, entity_epoch=old.epoch,
            entity_type=old.type, entity_kind=old.kind, entity_name=old.name,
            entity_x=old.x, entity_y=old.y, entity_z=old.z,
            position_source='last_known',
            visibility_end_reason='npc_update_visibility_bit',
            -- This bit can mean out-of-range, despawn, or death.
        }
    end

    -- 0x00E only has meaningful coordinates when bit 0 of Mask is set.
    local has_position = packet_id ~= 0x00E or hasbit(mask, 1)
    local x = has_position and finite(parsed['X']) or nil
    local y = has_position and finite(parsed['Y']) or nil
    local z = has_position and finite(parsed['Z']) or nil
    if not (x and y and z) then x, y, z = nil, nil, nil end
    if not old and not x then return nil end

    local typ = packet_id == 0x05B and finite(parsed['Type']) or (old and old.type)
    local name = old and old.name or nil
    if packet_id == 0x00E and hasbit(mask, 8) then
        name = clean_name(parsed['Name']) or name
    end
    if not name and type(lookup) == 'function' then
        local ok, mob = pcall(lookup, id)
        if ok and type(mob) == 'table' and
           (mob.id == nil or tonumber(mob.id) == id) and
           (mob.index == nil or tonumber(mob.index) == index) then
            name = clean_name(mob.name)
        end
    end

    if not old then
        self.epoch = self.epoch + 1
        old = {epoch=self.epoch, type=typ, kind=kind_for(typ), name=name,
            x=x, y=y, z=z, last_emitted=now}
        self.entities[key] = old
        return {
            observation_kind='first_seen', source_packet=packet_id,
            entity_id=id, entity_index=index, entity_epoch=old.epoch,
            entity_type=typ, entity_kind=old.kind, entity_name=name,
            entity_x=x, entity_y=y, entity_z=z, position_source='packet'
        }
    end

    local changed_name = name ~= nil and name ~= old.name
    local changed_position = moved(old, x, y, z)
    if x then old.x, old.y, old.z = x, y, z end
    if name then old.name = name end
    if typ then old.type, old.kind = typ, kind_for(typ) end

    local kind
    if packet_id == 0x05B then kind = 'reseen'
    elseif changed_position then kind = 'position_update'
    elseif changed_name then kind = 'identity_update'
    elseif now - old.last_emitted >= UPDATE_HEARTBEAT then kind = 'still_visible'
    else return nil end
    old.last_emitted = now
    return {
        observation_kind=kind, source_packet=packet_id,
        entity_id=id, entity_index=index, entity_epoch=old.epoch,
        entity_type=old.type, entity_kind=old.kind, entity_name=old.name,
        entity_x=old.x, entity_y=old.y, entity_z=old.z,
        position_source=x and 'packet' or 'last_known'
    }
end

return M
