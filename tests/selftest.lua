-- Run from repo root: lua tests/selftest.lua
package.path = './?.lua;' .. package.path
local capture = require('capture')
local clock, zone, x = 1000, 100, 0
local rows = {}
local fh = {write=function(self,s) rows[#rows+1]=s; return self end,flush=function() return true end,close=function() return true end}
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
-- Daily or 64 MiB file rotation must preserve capture without a logout.
local current_files = {}
local real_open = io.open
io.open=function(path)
    local file = {parts={}, path=path, closed=false}
    file.write=function(self, data) self.parts[#self.parts+1]=data; return self end
    file.flush=function() return true end
    file.close=function(self) self.closed=true; return true end
    current_files[#current_files+1]=file
    return file
end
local rotate_clock=5000
local r=capture.new({
 now=function() return rotate_clock end,
 player=function() return {name='Test',id=2,status=0} end,
 info=function() return {zone=101} end,
 self_mob=function() return {x=5,y=5,z=0,facing=0} end,
 path='./',dir_exists=function() return true end,create_dir=function() end,
 parse=function() return {} end,log=function() end
})
r:start()
assert(r.active and #current_files==1)
local first_id=r.session
rotate_clock=rotate_clock+24*60*60+1
r:tick()
assert(r.active and r.session~=first_id and #current_files==2)
assert(current_files[1].closed and not current_files[2].closed)
local before=table.concat(current_files[1].parts)
assert(before:find('"event":"session_stop"',1,true))
assert(before:find('"reason":"rotation_duration"',1,true))
r:flush()
local second=table.concat(current_files[2].parts)
assert(second:find('"event":"session_start"',1,true))
assert(second:find('"recorder_version":"0.2.3"',1,true))
-- Fail replacement opening without losing the current recording.
local count=#current_files
local original_open=io.open
io.open=function() return nil,'disk unavailable' end
assert(not r:rotate('rotation_size'))
assert(r.active and not current_files[2].closed and r.session~=first_id)
io.open=original_open
r.bytes_written=64*1024*1024
rotate_clock=rotate_clock+0.4
r:tick()
assert(r.active and #current_files==count+1 and current_files[2].closed)
assert(table.concat(current_files[2].parts):find('"reason":"rotation_size"',1,true))
r:stop('test')
assert(not r.active and current_files[#current_files].closed)
io.open=real_open

print('UnifiedCapture selftest OK')
