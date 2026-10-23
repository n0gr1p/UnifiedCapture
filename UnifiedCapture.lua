_addon.name = 'UnifiedCapture'
_addon.author = 'n0gr1p'
_addon.version = '0.1.0'
_addon.commands = {'ucapture', 'ucap'}

local capture = require('capture')
local socket = require('socket')
local packets = require('packets')
local recorder = capture.new({
    now = socket.gettime,
    player = function() return windower.ffxi.get_player() end,
    info = function() return windower.ffxi.get_info() end,
    self_mob = function() return windower.ffxi.get_mob_by_target('me') end,
    target = function(id) return windower.ffxi.get_mob_by_id(id) end,
    path = windower.addon_path,
    dir_exists = windower.dir_exists,
    create_dir = windower.create_dir,
    parse = function(direction, data) return packets.parse(direction, data) end,
    log = function(message) windower.add_to_chat(207, '[UnifiedCapture] ' .. message) end,
})

windower.register_event('prerender', function() recorder:tick() end)
windower.register_event('zone change', function(new_zone, old_zone)
    recorder:zone_change(new_zone, old_zone)
end)
windower.register_event('outgoing chunk', function(id, original, modified, injected, blocked)
    recorder:packet('outgoing', id, modified or original, injected, blocked)
end)
windower.register_event('incoming chunk', function(id, original, modified, injected, blocked)
    recorder:packet('incoming', id, modified or original, injected, blocked)
end)
windower.register_event('logout', function() recorder:stop('logout') end)
windower.register_event('unload', function() recorder:stop('unload') end)
windower.register_event('addon command', function(command)
    command = tostring(command or 'status'):lower()
    if command == 'on' or command == 'start' then recorder:start()
    elseif command == 'off' or command == 'stop' then recorder:stop('manual')
    elseif command == 'status' then recorder:status()
    else windower.add_to_chat(207, '[UnifiedCapture] //ucap on|off|status') end
end)
recorder:start()
