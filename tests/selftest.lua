-- Run from repo root: lua tests/selftest.lua
package.path = './?.lua;' .. package.path
local capture = require('capture')
local clock, zone, x = 1000, 100, 0
local rows = {}
local fh = {write=function(_,s) rows[#rows+1]=s end,flush=function() end,close=function() end}
local oldopen = io.open
io.open=function() return fh end
local c=capture.new({
 now=function() return clock end,
 player=function() return {name='Test',id=1,status=0} end,
 info=function() return {zone=zone} end,
 self_mob=function() return {x=x,y=0,z=0,facing=0} end,
 target=function() return {name='NPC',x=4,y=3,z=2} end,
 path='./',dir_exists=function() return true end,create_dir=function() end,
 parse=function() return {['Target']=123,['Menu ID']=17,['Option Index']=2} end,log=function() end,
})
c:start(); assert(c.active and c.seq==2)
clock=clock+0.1; x=0.01; c:tick(); assert(c.seq==2,'throttle')
clock=clock+0.4; x=2; c:tick(); assert(c.seq==3,'movement')
clock=clock+0.4; x=100; c:tick(); assert(c.seq==5,'jump')
c:packet('outgoing',0x05B,'payload',false,false); assert(c.seq>=6,'packet')
local log=table.concat(rows)
assert(log:find('"packet_kind":"menu_selection"',1,true))
assert(not log:find('payload',1,true))
zone=101; c:zone_change(101,100); c:tick(true)
c:stop('test'); assert(not c.active)
io.open=oldopen
print('UnifiedCapture selftest OK')
