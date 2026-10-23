-- Passive, read-only navigation observations. Lua 5.1 compatible.
local M = {}
local P = {}
P.__index = P
local SAMPLE_INTERVAL = 0.30
local MIN_DISTANCE = 0.35
local MAX_GAP = 4.0
local WARP_DISTANCE = 35.0
local FLUSH_INTERVAL = 2.0
local PACKETS = {
    outgoing = { [0x01A] = 'action_request', [0x05B] = 'menu_selection', [0x05C] = 'menu_selection', [0x05E] = 'zone_request' },
    incoming = { [0x034] = 'npc_menu', [0x05B] = 'menu_response', [0x00A] = 'zone_packet' },
}
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
        last_flush=0, seq=0, session=nil, file=nil, failures=0}, P)
end
function P:emit(kind, data)
    if not self.active then return end
    self.seq = self.seq + 1
    local e = {event=kind, time=self.env.now(), sequence=self.seq, session=self.session}
    local s = snapshot(self)
    if s then
        for k,v in pairs(s) do e[k] = v end
    end
    for k,v in pairs(data or {}) do e[k] = v end
    self.pending[#self.pending+1] = encode(e) .. '\n'
    if #self.pending >= 64 then self:flush() end
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
    self.last_sample, self.last_flush = 0, self.env.now()
    self:emit('session_start', {schema=1})
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
    self:emit('zone_change', {new_zone=finite(new_zone), old_zone=finite(old_zone)})
    self.last = nil
    self:flush()
end
function P:packet(direction, id, bytes, injected, blocked)
    if not self.active or blocked then return end
    local kind = PACKETS[direction] and PACKETS[direction][id]
    if not kind or type(bytes) ~= 'string' then return end
    local event = {direction=direction, packet_id=id, packet_kind=kind,
        injected=injected == true, byte_count=#bytes}
    local ok, parsed = pcall(self.env.parse, direction, bytes)
    if ok and type(parsed) == 'table' then
        -- Whitelist only; never persist raw packets, chat or arbitrary payload fields.
        event.target_id = finite(parsed['Target'] or parsed['NPC'])
        event.target_index = finite(parsed['Target Index'])
        event.category = finite(parsed['Category'])
        event.param = finite(parsed['Param'])
        event.menu_id = finite(parsed['Menu ID'])
        event.option_index = finite(parsed['Option Index'])
        event.zone_id = finite(parsed['Zone'])
        if event.target_id and self.env.target then
            local success, mob = pcall(self.env.target, event.target_id)
            if success and type(mob) == 'table' then
                event.target_name = tostring(mob.name or '')
                event.target_x = finite(mob.x)
                event.target_y = finite(mob.y)
                event.target_z = finite(mob.z)
            end
        end
    else event.decode_failed = true end
    self:tick(true)
    self:emit('packet_observation', event)
    self:flush()
end
function P:stop(reason)
    if not self.active then return end
    self:tick(true)
    self:emit('session_stop', {reason=reason or 'manual'})
    self:flush()
    if self.file then pcall(function() self.file:close() end) end
    self.file, self.active, self.last = nil, false, nil
    self.env.log('Stopped; ' .. tostring(self.seq) .. ' events')
end
function P:status()
    self.env.log(self.active and ('Recording ' .. self.session .. ', events=' .. self.seq .. ', queued=' .. #self.pending)
        or ('Inactive; write_failures=' .. self.failures))
end
return M
