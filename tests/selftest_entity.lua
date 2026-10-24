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
assert(obs(0x05B, {ID=1001, Index=22, Type=3, X=10, Y=20, Z=1}, 1001) == nil,
    'identical appearance packets are not new distinct mob observations')
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

-- Monster classification does not require a 0x05B packet: Windower's
-- mob.spawn_type=16 can enrich an incoming 0x00E first-seen observation.
local classified=t:observe(0x00E,{NPC=2001,Index=77,Mask=9,Name='Blood Toad',
    X=4,Y=5,Z=6},262,1100,function(id)
    assert(id==2001)
    return {id=id,index=77,name='Blood Toad',spawn_type=16}
end)
assert(classified.entity_kind=='monster')
assert(classified.entity_kind_source=='windower_spawn_type')
assert(classified.windower_spawn_type==16 and classified.entity_name=='Blood Toad')
local late=t:observe(0x05B,{ID=2002,Index=78,Type=0,X=4,Y=5,Z=6},262,1101,
    function() return {id=2002,index=78,spawn_type=16,name='Broadleaf Palm'} end)
assert(late.entity_kind=='monster' and late.entity_type==0,
    'packet type and Windower spawn_type use different enumerations')
assert(t:observe(0x05B,{ID=2002,Index=78,Type=0,X=4,Y=5,Z=6},262,1102,
    function() return nil end) == nil)
local visible=t:observe(0x05B,{ID=2002,Index=78,Type=0,X=4,Y=5,Z=6},262,1107,
    function() return nil end)
assert(visible.observation_kind=='still_visible',
    'periodic observations still support empirical visibility measurements')

-- Death context is retained in memory for the disappearance event only.
-- HP/status fluctuations must not create combat telemetry.
local d=t:observe(0x00E,{NPC=3001,Index=79,Mask=9,Name='Cinder Crab',
    X=7,Y=8,Z=9},262,1200,function() return {id=3001,index=79,spawn_type=16} end)
assert(d.entity_kind=='monster')
assert(t:observe(0x00E,{NPC=3001,Index=79,Mask=4,['HP %']=91,Status='Engaged'},
    262,1201,function() return nil end)==nil)
assert(t:observe(0x00E,{NPC=3001,Index=79,Mask=4,['HP %']=0,Status='Dead'},
    262,1202,function() return nil end)==nil)
local dead=t:observe(0x00E,{NPC=3001,Index=79,Mask=32},
    262,1202.2,function() return nil end)
assert(dead.observation_kind=='last_seen' and dead.last_hp_percent==0)
assert(dead.death_signal=='recent_zero_hp' and dead.disappearance_cause=='unknown')
assert(dead.last_entity_status=='Dead')
local live=t:observe(0x00E,{NPC=3002,Index=80,Mask=9,Name='Zoldeff Jagil',
    X=17,Y=18,Z=9},262,1300,function() return nil end)
assert(live)
t:observe(0x00E,{NPC=3002,Index=80,Mask=4,['HP %']=100,Status='Idle'},
    262,1300.2)
local lost=t:observe(0x00E,{NPC=3002,Index=80,Mask=32},262,1301)
assert(lost.observation_kind=='last_seen' and lost.death_signal==nil,
    'no death evidence must not be misread as range exit')
assert(lost.last_hp_percent==100)
-- A stale zero HP signal also cannot imply the latest exit was a death.
t:observe(0x00E,{NPC=3003,Index=81,Mask=1,X=18,Y=19,Z=8},262,1400)
t:observe(0x00E,{NPC=3003,Index=81,Mask=4,['HP %']=0},262,1401)
local stale=t:observe(0x00E,{NPC=3003,Index=81,Mask=32},262,1420)
assert(stale and stale.death_signal==nil and stale.last_hp_age>=19)

-- Canonical-name regression using the oscillation observed in Marjami Ravine.
-- A truncated 0x00E name is provisional; it must be replaced by a verified
-- Windower mob name when the cache becomes ready, then never downgraded.
local names=Tracker.new()
local cache_ready=false
local lookup_calls=0
local function cliff_lookup(id)
    lookup_calls=lookup_calls+1
    if not cache_ready then return nil end
    return {id=id,index=201,name='Cliffclinger Toad',spawn_type=16}
end
local short=names:observe(0x00E,{NPC=8101,Index=201,Mask=9,
    Name='CliffclingerTo',X=4,Y=5,Z=6},266,1600,cliff_lookup)
assert(short.entity_name=='CliffclingerTo' and
    short.entity_name_source=='packet_update')
assert(names:observe(0x00E,{NPC=8101,Index=201,Mask=8,
    Name='CliffclingerTo'},266,1600.4,cliff_lookup)==nil)
assert(lookup_calls==1,'initial provisional name lookup should be throttled')
cache_ready=true
local canonical=names:observe(0x00E,{NPC=8101,Index=201,Mask=8,
    Name='CliffclingerTo'},266,1603.1,cliff_lookup)
assert(canonical.observation_kind=='identity_update')
assert(canonical.entity_name=='Cliffclinger Toad' and
    canonical.entity_name_source=='windower_mob')
local churn=names:observe(0x00E,{NPC=8101,Index=201,Mask=8,
    Name='CliffclingerTo'},266,1603.6,cliff_lookup)
assert(churn==nil,'later truncated packet cannot overwrite canonical name')
assert(lookup_calls==2,'resolved canonical names need no further lookups')
local heartbeat=names:observe(0x00E,{NPC=8101,Index=201,Mask=0},
    266,1609,cliff_lookup)
assert(heartbeat.observation_kind=='still_visible' and
    heartbeat.entity_name=='Cliffclinger Toad')
local disappeared=names:observe(0x00E,{NPC=8101,Index=201,Mask=32},
    266,1610,cliff_lookup)
assert(disappeared.entity_name=='Cliffclinger Toad' and
    disappeared.entity_name_source=='windower_mob')

-- When the cache is immediately ready, the very first sighting uses the
-- complete name; a subsequent NPC packet cannot alter it.
local dirt=names:observe(0x00E,{NPC=8102,Index=202,Mask=9,
    Name='DirtcakedJagil',X=7,Y=8,Z=9},266,1700,function(id)
        return {id=id,index=202,name='Dirtcaked Jagil',spawn_type=16}
    end)
assert(dirt.entity_name=='Dirtcaked Jagil' and
    dirt.entity_name_source=='windower_mob')
assert(names:observe(0x00E,{NPC=8102,Index=202,Mask=8,
    Name='DirtcakedJagil'},266,1701)==nil)

-- A fully identified monster is still eligible for cache-name enrichment
-- when only a truncated packet name was available earlier.
local lookup_ready=false
local known=names:observe(0x05B,{ID=8103,Index=203,Type=3,
    X=1,Y=2,Z=3},266,1800,function() return nil end)
local provisional=names:observe(0x00E,{NPC=8103,Index=203,Mask=8,
    Name='WhisperingTwit'},266,1800.2,function() return nil end)
assert(provisional.entity_name_source=='packet_update')
lookup_ready=true
local complete=names:observe(0x00E,{NPC=8103,Index=203,Mask=8,
    Name='WhisperingTwit'},266,1803.5,function(id)
        return {id=id,index=203,name='Whispering Twitherym',spawn_type=16}
    end)
assert(complete.entity_kind=='monster' and
    complete.entity_name=='Whispering Twitherym' and
    complete.entity_name_source=='windower_mob')

-- The mob lookup must identify the exact entity, not a recycled index.
local mismatch=names:observe(0x00E,{NPC=8104,Index=204,Mask=9,
    Name='SmallPacketName',X=1,Y=1,Z=1},266,1900,function(id)
        return {id=id,index=999,name='Unrelated Entity',spawn_type=16}
    end)
assert(mismatch.entity_name=='SmallPacketName' and
    mismatch.entity_name_source=='packet_update')

-- Recorder-level tests for schema, observer-at-packet synchronization,
-- classification, multi-packet menu correlation, and zone/session reset.
local capture = require('capture')
local clock, zone, x = 2000, 256, 5
local rows, decoded, log_messages = {}, {}, {}
local fakefile = {write=function(self,s) rows[#rows+1]=s; return self end,flush=function() return true end,close=function() return true end}
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
    item=function(bag, slot)
        if bag ~= 0 then return nil end
        if slot == 4 then return {id=41001} end -- Ring fixture
        if slot == 7 then return {id=41002} end -- Scroll fixture
        if slot == 8 then error('inventory unavailable') end
    end,
    item_name=function(id)
        if id == 41001 then return 'Warp Ring' end
        if id == 41002 then return 'Instant Warp' end
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
decoded.request={Target=999,['Target Index']=33,Category=0,Param=0}
decoded.ring={Player=9,Bag=0,Slot=4}
decoded.scroll={Player=9,Bag=0,Slot=7}
decoded.missing_item={Player=9,Bag=0,Slot=8}
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

-- Warp Ring and Instant Warp use outgoing 0x037; resolve by bag and slot
-- without recording full inventory or claiming that the warp succeeded.
local before_item = c.seq
c:packet('outgoing',0x037,'ring',false,true)
assert(c.seq == before_item, 'blocked item use must not be recorded')
c:packet('outgoing',0x037,'ring',false,false)
c:packet('outgoing',0x037,'scroll',true,false)
c:packet('outgoing',0x037,'missing_item',false,false)
assert(c.seq >= before_item+3, 'item requests remain captured if lookup fails')

-- Recreate a Home Point/Waypoint style sequence with no inbound menu open.
-- Incoming 0x052 release does not necessarily end that interaction; the
-- second choice may arrive milliseconds later.
clock=clock+10
c:packet('outgoing',0x01A,'request',false,false)
local seeded=c.menu.id
c:packet('outgoing',0x05B,'choice',false,false)
assert(c.menu.id==seeded and not c.menu.has_open)
c:packet('incoming',0x052,'release',false,false)
clock=clock+0.015
c:packet('incoming',0x05C,'params',false,false)
c:packet('outgoing',0x05B,'choice',false,false)
assert(c.menu.id==seeded,'post-release menu continuation must retain request ID')
c:packet('incoming',0x052,'release',false,false)
clock=clock+3
c:packet('outgoing',0x05B,'choice',false,false)
assert(c.menu.id~=seeded,'late choice must open separate interaction')
-- A real Task Delegator menu was left open for 129.5 seconds in the
-- v0.2.2 acceptance trace. It must retain its interaction ID even though
-- ordinary unconfirmed/orphan contexts expire after 120 seconds.
clock=clock+1
c:packet('outgoing',0x01A,'request',false,false)
c:packet('incoming',0x032,'menu32',false,false)
local long_open=c.menu.id
assert(c.menu.has_open)
clock=clock+130
c:packet('outgoing',0x05B,'choice',false,false)
assert(c.menu.id==long_open and c.menu.has_open,
    'real open menu must survive 130 seconds of user dwell')
c:packet('incoming',0x052,'release',false,false)
clock=clock+3
c:packet('outgoing',0x05B,'choice',false,false)
assert(c.menu.id~=long_open,
    'released menu must not retain the long-open timeout')

local before = c.seq
c:packet('incoming',0x05B,'spawn',false,true)
assert(c.seq == before, 'blocked packets not recorded')
zone=262; clock=clock+1; c:zone_change(262,256)
c:packet('incoming',0x05B,'spawn',false,false)
c:stop('test')
io.open = oldopen
local jsonl=table.concat(rows)
assert(jsonl:find('"schema":2',1,true))
assert(jsonl:find('"recorder_version":"0.2.4"',1,true))
assert(jsonl:find('"packet_id":91',1,true) and jsonl:find('"entity_kind":"monster"',1,true))
assert(jsonl:find('"observer_x":5',1,true), 'original observer position preserved')
assert(jsonl:find('"observation_kind":"last_seen"',1,true))
assert(jsonl:find('"reason":"zone_event"',1,true), 'zone reset recorded')
assert(jsonl:find('"packet_kind":"npc_menu_string"',1,true))
assert(jsonl:find('"packet_kind":"item_use_request"',1,true))
assert(jsonl:find('"item_name":"Warp Ring"',1,true) and jsonl:find('"item_id":41001',1,true))
assert(jsonl:find('"item_name":"Instant Warp"',1,true) and jsonl:find('"item_id":41002',1,true))
assert(jsonl:find('"item_slot":4',1,true) and jsonl:find('"item_bag":0',1,true))
assert(jsonl:find('"item_slot":8',1,true), 'inventory failures keep slot evidence')
assert(not jsonl:find('"item_name":"inventory unavailable"',1,true))
assert(jsonl:find('"interaction_stage":"menu_open"',1,true))
assert(jsonl:find('"interaction_stage":"menu_choice"',1,true))
assert(jsonl:find('"interaction_stage":"warp_request"',1,true))
assert(jsonl:find('"interaction_stage":"menu_release"',1,true))
assert(jsonl:find('"interaction_stage":"menu_parameters"',1,true))
assert(jsonl:find('"interaction_correlation":"matched_menu"',1,true))
assert(jsonl:find('"interaction_stage":"interaction_request"',1,true))
assert(jsonl:find('"interaction_correlation":"matched_request"',1,true))
assert(jsonl:find('"interaction_origin":"interaction_request"',1,true))
assert(not jsonl:find('menu_response',1,true), 'spawn never called menu response')
assert(not jsonl:find('"spawn"',1,true), 'raw fixture bytes never persisted')
assert(not c.active)
print('Entity and menu P0/P1 selftest OK')
