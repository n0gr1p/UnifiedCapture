-- NAV-11: same-client outcome -> immutable JSONL recorder bridge.
package.path='./?.lua;'..package.path
local nav=require('nav_outcome_record')
local capture=require('capture')
local function eq(a,b,msg)
    if a~=b then error((msg or 'different')..' expected='..
        tostring(b)..' actual='..tostring(a)) end
end
local valid={'tj-evt-0001','tj-run-0001','4611686018427387903',
    'traversed','completed_edge','1','0','1'}
local function copy(t)
    local out={}
    for i=1,#t do out[i]=t[i] end
    return out
end
local ok,why=nav.decode(valid,{name='Terrasjr'},{zone=247})
eq(type(ok),'table','valid local event')
eq(ok.nav_edge_id_str,'4611686018427387903','exact largest ID')
eq(ok.nav_confirmed,true,'confirmed traversal')
local bad={
    {index=3,value='4611686018427387904'}, -- above 62-bit ceiling
    {index=3,value='1e16'}, -- scientific notation forbidden
    {index=3,value='9007199254740992.0'}, -- not a decimal integer
    {index=4,value='blocked'},
    {index=5,value='combat'},
    {index=6,value='0'},
    {index=7,value='1'},
    {index=8,value='1'},
}
for i,mutation in ipairs(bad) do
    local changed=copy(valid);changed[mutation.index]=mutation.value
    eq(nav.decode(changed,{name='Terrasjr'},{zone=247}),nil,
        'reject malformed payload '..i)
end
eq(nav.decode(valid,{name='Redrogue'},{zone=247}),nil,
    'do not accept outcomes from other sixbox clients')
eq(nav.decode(valid,{name='Terrasjr'},{zone=248}),nil,
    'other zone blocked')
local clock,character,zone=1000,'Terrasjr',247
local rows={}
local fh={write=function(self,part) rows[#rows+1]=part;return self end,
    flush=function() return true end,close=function()return true end}
local old_open=io.open
io.open=function() return fh end
local recorder=capture.new({
    now=function() return clock end,
    player=function()return {name=character,id=1,status=0} end,
    info=function()return {zone=zone} end,
    self_mob=function()return {x=0,y=0,z=0} end,
    path='./',dir_exists=function()return true end,
    create_dir=function()end,parse=function()return {} end,
    log=function()end,
})
eq(recorder:nav_outcome(valid),false,'disabled capture cannot record')
recorder:start()
local before=recorder.seq
eq(recorder:nav_outcome(valid),true,'valid outcome recorded')
eq(recorder.seq,before+1,'exactly one output event')
local log=table.concat(rows)
assert(log:find('"event":"nav_edge_outcome"',1,true),
    'actual session JSONL must contain the event')
assert(log:find('"nav_edge_id_str":"4611686018427387903"',1,true),
    'full UCNV ID must be preserved as JSON string')
assert(log:find('"nav_confirmed":true',1,true),'confirmed is boolean')
assert(log:find('"character":"Terrasjr"',1,true),'actor captured')
local prior=recorder.seq
character='Redrogue'
eq(recorder:nav_outcome(valid),false,'wrong player cannot record')
character='Terrasjr'
zone=248
eq(recorder:nav_outcome(valid),false,'wrong zone cannot record')
eq(recorder.seq,prior,'bad events do not advance sequence')
zone=247
local interrupted=copy(valid)
interrupted[1]='tj-evt-0002'
interrupted[4]='interrupted';interrupted[5]='combat';interrupted[6]='0'
eq(recorder:nav_outcome(interrupted),true,'interruption retained')
log=table.concat(rows)
assert(log:find('"nav_kind":"interrupted"',1,true))
assert(log:find('"nav_confirmed":false',1,true))
recorder:stop('test')
io.open=old_open
print('selftest_nav_outcome: OK')
