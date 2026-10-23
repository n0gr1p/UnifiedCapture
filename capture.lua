-- Passive, read-only navigation observations. Lua 5.1 compatible.
local M = {}
local entity_tracker = require('entity_tracker')
local P = {}
P.__index = P
local SAMPLE_INTERVAL = 0.30
local MIN_DISTANCE = 0.35
local MAX_GAP = 4.0
local WARP_DISTANCE = 35.0
local FLUSH_INTERVAL = 2.0
local PACKETS = {
    outgoing = { [0x01A] = 'action_request', [0x05B] = 'menu_selection', [0x05C] = 'warp_request', [0x05E] = 'zone_request' },
    incoming = { [0x032] = 'npc_menu', [0x033] = 'npc_menu_string',
        [0x034] = 'npc_menu', [0x052] = 'npc_release',
        [0x05C] = 'menu_parameters', [0x00A] = 'zone_packet' },
}
local ENTITY_PACKETS = { [0x05B] = true, [0x00E] = true, [0x065] = true }
local MENU_TTL = 120.0
local function finite(v)
    v = tonumber(v)
    if not v or v ~= v or v == math.huge or v == -math.huge then return nil end
    return v
end
local function str(s)
    s = tostring(s or '')
    return '"' .. s:gsub('\\', '\\\\'):gsub('"', '\\"'):gsub('\n', '\\n'):gsub('\r', '\\r'):gsub('\t', '\\t'):gsub('[%z\1-\31]', '?') .. '"'
end
local function encode(t)
    local fields = {}
    for k, v in pairs(t) do
        local value
        if type(v) == 'string' then value = str(v)
        elseif type(v) == 'number' then value = finite(v) and tostring(v) or 'null'
        elseif type(v) == 'boolean' then value = tostring(v)
        end
        if value then fields[#fields + 1] = str(k) .. ':' .. value end
    end
    table.sort(fields)
    return '{' .. table.concat(fields, ',') .. '}'
end
local function safe(s)
    return tostring(s or 'unknown'):lower():gsub('[^%w_%-]', '_'):sub(1, 60)
end
local function snapshot(self)
    local ok, player = pcall(self.env.player)
    if not ok or not player or not player.name then return nil end
    local oi, info = pcall(self.env.info)
    local om, mob = pcall(self.env.self_mob)
    if not oi or not info or not om or not mob then return nil end
    local x, y, z = finite(mob.x), finite(mob.y), finite(mob.z)
    local zone = finite(info.zone)
    if not x or not y or not z or not zone or zone == 0 then return nil end
    return {character = player.name, zone = zone, x = x, y = y, z = z,
        heading = finite(mob.facing), status = finite(player.status),
        player_id = finite(player.id)}
end
local function distance(a, b)
    local x, y, z = a.x-b.x, a.y-b.y, a.z-b.z
    return math.sqrt(x*x+y*y+z*z)
end
function M.new(env)
    return setmetatable({env=env, active=false, last=nil, pending={}, last_sample=0,
        last_flush=0, seq=0, session=nil, file=nil, failures=0,
        entities=entity_tracker.new(), entity_zone=nil, menu=nil, menu_counter=0}, P)
end
function P:emit(kind, data, at, observer)
    if not self.active then return end
    self.seq = self.seq + 1
    local e = {event=kind, time=at or self.env.now(), sequence=self.seq, session=self.session}
    local s = observer or snapshot(self)
    if s then
        for k,v in pairs(s) do e[k] = v end
        if kind == 'entity_observation' then
            e.observer_x, e.observer_y, e.observer_z = s.x, s.y, s.z
        end
    end
    for k,v in pairs(data or {}) do e[k] = v end
    self.pending[#self.pending+1] = encode(e) .. '\n'
    if #self.pending >= 64 then self:flush() end
end

function P:check_entity_zone(zone, reason, at, observer)
    if not zone or self.entity_zone == zone then return end
    local prior = self.entity_zone
    local tracked = self.entities:reset()
    self.entity_zone = zone
    self.menu = nil
    if prior then
        self:emit('entity_visibility_reset', {
            reason=reason, previous_zone=prior, new_zone=zone, tracked_count=tracked
        }, at, observer)
    end
end
function P:flush()
    if not self.file or #self.pending == 0 then return end
    local data = table.concat(self.pending)
    local ok = pcall(function() self.file:write(data); self.file:flush() end)
    if ok then self.pending = {} else
        self.failures = self.failures + 1
        if self.env.log then self.env.log('Write failure; capture stopped (buffer retained)') end
        self.active = false
    end
    self.last_flush = self.env.now()
end
function P:start()
    if self.active then return end
    local s = snapshot(self)
    if not s then
        self.env.log('Waiting for character and valid position; retry //ucap on')
        return
    end
    local base = self.env.path .. 'data/'
    local dir = base .. 'captures/'
    if not self.env.dir_exists(base) then self.env.create_dir(base) end
    if not self.env.dir_exists(dir) then self.env.create_dir(dir) end
    local stamp = os.date('!%Y%m%dT%H%M%SZ')
    self.session = safe(s.character) .. '_' .. stamp .. '_' .. tostring(math.floor((self.env.now() % 1)*1000000))
    local f, err = io.open(dir .. self.session .. '.jsonl', 'a')
    if not f then self.env.log('Cannot open capture: ' .. tostring(err)); return end
    self.file, self.active = f, true
    self.last, self.pending, self.seq = nil, {}, 0
    self.entities = entity_tracker.new()
    self.entity_zone, self.menu, self.menu_counter = nil, nil, 0
    self.last_sample, self.last_flush = 0, self.env.now()
    self:emit('session_start', {schema=2, recorder_version='0.2.1'})
    self:tick(true)
    self.env.log('Recording to data/captures/' .. self.session .. '.jsonl')
end
function P:tick(force)
    if not self.active then return end
    local now = self.env.now()
    if not force and now-self.last_sample < SAMPLE_INTERVAL then return end
    self.last_sample = now
    local s = snapshot(self)
    if s then
        self:check_entity_zone(s.zone, 'snapshot_zone_change', now, s)
        if self.last and (s.zone ~= self.last.zone or s.character ~= self.last.character) then
            self:emit('discontinuity', {reason='zone_or_character_change',
                previous_zone=self.last.zone, previous_character=self.last.character})
            self.last = nil
        end
        if not self.last then
            self:emit('position', {reason='anchor'})
            self.last = s
            self.last.time = now
        else
            local d = distance(s,self.last)
            local elapsed = now - self.last.time
            if d > WARP_DISTANCE then
                self:emit('discontinuity', {reason='large_position_jump', distance=d})
                self:emit('position', {reason='new_anchor'})
                self.last = s; self.last.time = now
            elseif d >= MIN_DISTANCE or elapsed >= MAX_GAP then
                self:emit('position', {reason=d >= MIN_DISTANCE and 'moving' or 'heartbeat'})
                self.last = s; self.last.time = now
            end
        end
    end
    if now-self.last_flush >= FLUSH_INTERVAL then self:flush() end
end
function P:zone_change(new_zone, old_zone)
    if not self.active then return end
    local at, observer = self.env.now(), snapshot(self)
    self:emit('zone_change', {new_zone=finite(new_zone), old_zone=finite(old_zone)}, at, observer)
    self:check_entity_zone(finite(new_zone), 'zone_event', at, observer)
    -- Zone boundaries terminate pending NPC menus; no invented success/failure.
    self.menu = nil
    self.last = nil
    self:flush()
end
local function clean_name(name)
    if type(name) ~= 'string' then return nil end
    name = name:gsub('%z.*', ''):gsub('^%s+', ''):gsub('%s+$', '')
    return name ~= '' and name:sub(1, 96) or nil
end

local function npc_identity(self, id, index)
    if not id or not self.env.target then return nil end
    local ok, mob = pcall(self.env.target, id)
    if not ok or type(mob) ~= 'table' then return nil end
    if mob.id ~= nil and tonumber(mob.id) ~= id then return nil end
    if index and mob.index ~= nil and tonumber(mob.index) ~= index then return nil end
    return mob
end

local RELEASE_GRACE = 2.0
local MENU_OPEN_JOIN_WINDOW = 5.0
local function menu_is_current(self, at, target, menu_id, zone)
    local m = self.menu
    if not m or at - m.last > MENU_TTL then return false end
    if m.released_at and at - m.released_at > RELEASE_GRACE then return false end
    if zone and m.zone and zone ~= m.zone then return false end
    if target and m.target and target ~= m.target then return false end
    if menu_id and m.menu_id and menu_id ~= m.menu_id then return false end
    return true
end

local function begin_menu(self, at, target, menu_id, zone, origin)
    self.menu_counter = self.menu_counter + 1
    self.menu = {id=self.menu_counter, target=target, menu_id=menu_id,
        zone=zone, opened_at=at, last=at, origin=origin, has_open=false}
    return self.menu
end

local function observe_menu(self, direction, id, p, e, at, zone)
    -- Outgoing action category 0 is the preceding NPC interaction request;
    -- some Home Point/Waypoint dialogues have no observed 0x032/0x033/0x034.
    local is_request = direction == 'outgoing' and id == 0x01A and
        e.category == 0 and e.target_id ~= nil and e.target_id > 0
    local is_open = direction == 'incoming' and (id == 0x032 or id == 0x033 or id == 0x034)
    local is_choice = direction == 'outgoing' and (id == 0x05B or id == 0x05C)
    local is_release = direction == 'incoming' and id == 0x052
    local is_params = direction == 'incoming' and id == 0x05C
    if not (is_request or is_open or is_choice or is_release or is_params) then return end
    local target, menu_id = e.target_id, e.menu_id
    local current = menu_is_current(self, at, target, menu_id, zone)
    if is_request then
        begin_menu(self,at,target,nil,zone,'interaction_request')
        e.interaction_stage = 'interaction_request'
    elseif is_open then
        if not current or self.menu.released_at or
           (self.menu.origin ~= 'interaction_request' and
            at - self.menu.opened_at > MENU_OPEN_JOIN_WINDOW) then
            begin_menu(self,at,target,menu_id,zone,'menu_open')
        else
            self.menu.last=at
            self.menu.target=self.menu.target or target
            self.menu.menu_id=self.menu.menu_id or menu_id
        end
        self.menu.has_open=true
        e.interaction_stage='menu_open'
    elseif is_choice then
        if not current then
            begin_menu(self,at,target,menu_id,zone,'choice_without_open')
            e.interaction_correlation='choice_without_open'
        else
            self.menu.last=at
            self.menu.released_at=nil
            self.menu.target=self.menu.target or target
            self.menu.menu_id=self.menu.menu_id or menu_id
            e.interaction_correlation=self.menu.has_open and 'matched_menu' or
                (self.menu.origin=='interaction_request' and 'matched_request' or 'matched_choice')
        end
        e.interaction_stage=id==0x05C and 'warp_request' or 'menu_choice'
    elseif is_release then
        e.interaction_stage='menu_release'
    elseif is_params then
        e.interaction_stage='menu_parameters'
    end
    if menu_is_current(self,at,target,menu_id,zone) then
        local m=self.menu
        e.interaction_id=m.id
        e.interaction_target_id=m.target
        e.interaction_menu_id=m.menu_id
        e.interaction_origin=m.origin
    end
    if is_release and current then
        -- 0x052 release is not necessarily the final menu event; in the
        -- observed HP flow parameters and another choice follow ~8ms later.
        -- Retain context briefly to group this continuation, not forever.
        self.menu.last=at
        self.menu.released_at=at
    end
end

function P:packet(direction, id, bytes, injected, blocked)
    if not self.active or blocked or type(bytes) ~= 'string' then return end
    local is_entity = direction == 'incoming' and ENTITY_PACKETS[id]
    local kind = PACKETS[direction] and PACKETS[direction][id]
    if not is_entity and not kind then return end

    -- Snapshot at packet arrival, not after parsing or during a later render tick.
    local at, observer = self.env.now(), snapshot(self)
    -- Density/visibility observations require a synchronized observer
    -- location; never invent one from the previous movement sample.
    if is_entity and not observer then return end
    if observer then self:check_entity_zone(observer.zone, 'packet_zone_change', at, observer) end
    local ok, p = pcall(self.env.parse, direction, bytes)
    if not ok or type(p) ~= 'table' then
        self:emit('packet_observation', {direction=direction, packet_id=id,
            packet_kind=is_entity and 'entity_decode_failed' or kind,
            decode_failed=true, byte_count=#bytes}, at, observer)
        return
    end

    if is_entity then
        local zone = observer and observer.zone or self.entity_zone
        local result = self.entities:observe(id, p, zone, at, self.env.target)
        if result then
            result.direction = direction
            result.packet_id = id
            result.injected = injected == true
            self:emit('entity_observation', result, at, observer)
        end
        return
    end

    local target_id = finite(p['Target'] or p['Target ID'] or p['NPC'])
    local target_index = finite(p['Target Index'] or p['NPC Index'])
    local event = {direction=direction, packet_id=id, packet_kind=kind,
        injected=injected == true, byte_count=#bytes,
        target_id=target_id, target_index=target_index,
        category=finite(p['Category']), param=finite(p['Param']),
        menu_id=finite(p['Menu ID']), option_index=finite(p['Option Index']),
        zone_id=finite(p['Zone']),
        release_type=direction == 'incoming' and id == 0x052 and finite(p['Type']) or nil,
        zone_line=direction == 'outgoing' and id == 0x05E and finite(p['Zone Line']) or nil}
    if direction == 'outgoing' and id == 0x05B then
        event.automated_message = p['Automated Message'] == true or p['Automated Message'] == 1
    end
    local npc = npc_identity(self, target_id, target_index)
    event.target_name = clean_name(p['NPC Name']) or (npc and clean_name(npc.name))
    if npc then
        event.target_x, event.target_y, event.target_z =
            finite(npc.x), finite(npc.y), finite(npc.z)
    end
    observe_menu(self, direction, id, p, event, at, observer and observer.zone)
    self:tick(true)
    self:emit('packet_observation', event, at, observer)
    self:flush()
end
function P:stop(reason)
    if not self.active then return end
    self:tick(true)
    self:emit('session_stop', {reason=reason or 'manual'})
    self:flush()
    if self.file then pcall(function() self.file:close() end) end
    self.file, self.active, self.last = nil, false, nil
    self.entities:reset()
    self.entity_zone, self.menu = nil, nil
    self.env.log('Stopped; ' .. tostring(self.seq) .. ' events')
end
function P:status()
    self.env.log(self.active and ('Recording ' .. self.session .. ', events=' .. self.seq .. ', queued=' .. #self.pending)
        or ('Inactive; write_failures=' .. self.failures))
end
return M
