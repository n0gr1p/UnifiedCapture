-- P0/P1 regression tests for passive entity and menu telemetry.
package.path = './?.lua;' .. package.path
local Tracker = require('entity_tracker')
local t = Tracker.new()
local function obs(packet, fields, when)
    return t:observe(packet, fields, 262, when or 1000, function() return nil end)
end
local a = obs(0x05B, {ID=1001, Index=22, Type=3, X=10, Y=20, Z=1})
assert(a.observation_kind == 'first_seen' and a.entity_kind == 'monster')
assert(a.entity_x == 10 and a.entity_y == 20 and a.entity_z == 1)
local initial_epoch = a.entity_epoch
assert(obs(0x05B, {ID=1001, Index=22, Type=3, X=10, Y=20, Z=1}, 1001).observation_kind == 'reseen')
assert(obs(0x00E, {NPC=1001, Index=22, Mask=0, X=0, Y=0, Z=0}, 1002) == nil,
    'masked zero position must not erase valid coordinates')
local m = obs(0x00E, {NPC=1001, Index=22, Mask=1, X=11, Y=20, Z=1}, 1003)
assert(m.observation_kind == 'position_update' and m.entity_x == 11)
local named = obs(0x00E, {NPC=1001, Index=22, Mask=8, Name='Lesser Colibri'}, 1004)
assert(named.observation_kind == 'identity_update' and named.entity_name == 'Lesser Colibri')
assert(named.entity_x == 11 and named.position_source == 'last_known')
local periodic = obs(0x00E, {NPC=1001, Index=22, Mask=0}, 1010)
assert(periodic.observation_kind == 'still_visible')
assert(obs(0x065, {ID=9999, Index=23, X=1, Y=1, Z=1}) == nil,
    'unknown repositioning must not create a fake monster')
local r = obs(0x065, {ID=1001, Index=22, X=14, Y=20, Z=1}, 1011)
assert(r.observation_kind == 'position_update')
local gone = obs(0x00E, {NPC=1001, Index=22, Mask=32}, 1012)
assert(gone.observation_kind == 'last_seen' and gone.entity_x == 14)
assert(gone.visibility_end_reason == 'npc_update_visibility_bit')
assert(obs(0x00E, {NPC=1001, Index=22, Mask=32}) == nil, 'duplicate lost sight ignored')
local back = obs(0x05B, {ID=1001, Index=22, Type=3, X=15, Y=20, Z=1}, 1014)
assert(back.observation_kind == 'first_seen' and back.entity_epoch > initial_epoch,
    'new visibility window for returned identity')
assert(t:reset() == 1, 'zone/session reset clears identity cache')
assert(obs(0x05B, {ID=1001, Index=22, Type=0, X=15, Y=20, Z=1}).entity_kind == 'npc_or_casket')

-- Recorder-level tests for schema, observer-at-packet synchronization,
-- classification, multi-packet menu correlation, and zone/session reset.
local capture = require('capture')
local clock, zone, x = 2000, 256, 5
local rows, decoded, log_messages = {}, {}, {}
local fakefile = {write=function(_,s) rows[#rows+1]=s end,flush=function() end,close=function() end}
local oldopen = io.open
io.open = function() return fakefile end
local c = capture.new({
    now=function() return clock end,
    player=function() return {name='Niightsiide', id=9, status=0} end,
    info=function() return {zone=zone} end,
    self_mob=function() return {x=x,y=10,z=1,facing=1.57} end,
    target=function(id)
        if id == 999 then return {id=999,index=33,name='Waypoint',x=4,y=2,z=0} end
    end,
    path='./', dir_exists=function() return true end,
    create_dir=function() end,
    parse=function(direction, raw)
        assert(decoded[raw], 'unknown fixture ' .. raw)
        -- If the character moves while packet parsing, its timestamp/position
        -- must still represent the snapshot at entry to the callback.
        if raw == 'spawn' then x = 999 end
        return decoded[raw]
    end,
    log=function(s) log_messages[#log_messages+1] = s end
})
decoded.spawn={ID=101, Index=4, Type=3, X=40, Y=41, Z=2}
decoded.update={NPC=101, Index=4, Mask=9, Name='Colibri', X=42, Y=41, Z=2}
decoded.lost={NPC=101, Index=4, Mask=32}
decoded.menu32={NPC=999,['NPC Index']=33,['Menu ID']=88,Zone=256}
decoded.menu33={NPC=999,['NPC Index']=33,['Menu ID']=88,Zone=256,['NPC Name']='Waypoint'}
decoded.params={}
decoded.choice={Target=999,['Target Index']=33,['Menu ID']=88,['Option Index']=7,Zone=256}
decoded.warp={['Target ID']=999,['Target Index']=33,['Menu ID']=88,Zone=256}
decoded.release={Type=2,['Menu ID']=88}
decoded.zone={}
c:start()
assert(c.active)
c:packet('incoming',0x05B,'spawn',false,false)
c:packet('incoming',0x00E,'update',false,false)
c:packet('incoming',0x00E,'lost',false,false)
clock = clock + 1; x = 5
c:packet('incoming',0x032,'menu32',false,false)
c:packet('incoming',0x033,'menu33',false,false)
c:packet('incoming',0x05C,'params',false,false)
c:packet('outgoing',0x05B,'choice',false,false)
c:packet('outgoing',0x05C,'warp',false,false)
c:packet('incoming',0x052,'release',false,false)
local before = c.seq
c:packet('incoming',0x05B,'spawn',false,true)
assert(c.seq == before, 'blocked packets not recorded')
zone=262; clock=clock+1; c:zone_change(262,256)
c:packet('incoming',0x05B,'spawn',false,false)
c:stop('test')
io.open = oldopen
local jsonl=table.concat(rows)
assert(jsonl:find('"schema":2',1,true))
assert(jsonl:find('"packet_id":91',1,true) and jsonl:find('"entity_kind":"monster"',1,true))
assert(jsonl:find('"observer_x":5',1,true), 'original observer position preserved')
assert(jsonl:find('"observation_kind":"last_seen"',1,true))
assert(jsonl:find('"reason":"zone_event"',1,true), 'zone reset recorded')
assert(jsonl:find('"packet_kind":"npc_menu_string"',1,true))
assert(jsonl:find('"interaction_stage":"menu_open"',1,true))
assert(jsonl:find('"interaction_stage":"menu_choice"',1,true))
assert(jsonl:find('"interaction_stage":"warp_request"',1,true))
assert(jsonl:find('"interaction_stage":"menu_release"',1,true))
assert(jsonl:find('"interaction_stage":"menu_parameters"',1,true))
assert(jsonl:find('"interaction_correlation":"matched_menu"',1,true))
assert(not jsonl:find('menu_response',1,true), 'spawn never called menu response')
assert(not jsonl:find('spawn',1,true), 'raw packet contents never persisted')
assert(not c.active)
print('Entity and menu P0/P1 selftest OK')
