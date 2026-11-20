-- NAV-11: validate a local UnifiedCombat -> UnifiedCapture addon command.
-- No network, no game actions, no IPC broadcast to other clients.
local M = {}
local identifiers='^[%w_.:%-]+$'
local max_id='4611686018427387903' -- SQLite UCNV IDs fit in 62 bits.
local allowed={
    traversed={completed_edge=true},
    blocked={stalled=true,diverged=true,obstacle=true},
    interrupted={
        combat=true,casting=true,movement_owner=true,manual_stop=true,
        zoning=true,timeout=true,position_lost=true,disconnect=true,
        other=true,
    },
}
local function id(value)
    return type(value)=='string' and #value>=8 and #value<=128
        and value:match(identifiers)~=nil
end
local function index(value)
    if type(value)~='string' or not value:match('^%d+$')
        or #value>3 then return nil end
    local n=tonumber(value)
    if n and n>=0 and n<=100 then return n end
    return nil
end
function M.decode(args, player, info)
    if type(args)~='table' or #args~=8 then
        return nil,'INVALID_OUTCOME_FIELDS'
    end
    if type(player)~='table'
        or tostring(player.name or ''):lower()~='terrasjr'
        or type(info)~='table' or tonumber(info.zone)~=247 then
        return nil,'NAV_OUTCOME_WRONG_ACTOR_OR_ZONE'
    end
    local event,run,edge,kind,reason,confirmed,a,b=unpack(args)
    if not id(event) or not id(run) then
        return nil,'NAV_OUTCOME_INVALID_ID'
    end
    if type(edge)~='string'
        or edge:match('^[1-9]%d*$')==nil
        or #edge>19 or (#edge==19 and edge>max_id) then
        return nil,'NAV_OUTCOME_INVALID_EDGE'
    end
    local from,to=index(a),index(b)
    if not from or not to or to~=from+1 then
        return nil,'NAV_OUTCOME_INVALID_DIRECTION'
    end
    if not allowed[kind] or not allowed[kind][reason]
        or (kind=='interrupted' and confirmed~='0')
        or (kind~='interrupted' and confirmed~='1') then
        return nil,'NAV_OUTCOME_INVALID_RESULT'
    end
    -- Event names are flat because the existing recorder emits JSONL records
    -- with flat fields; original immutable source session/sequence stay intact.
    return {
        nav_event_id=event,nav_run_id=run,nav_edge_id_str=edge,
        nav_kind=kind,nav_reason=reason,
        nav_confirmed=(confirmed=='1'),
        nav_from_index=from,nav_to_index=to,
    }
end
return M
