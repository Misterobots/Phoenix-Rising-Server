"""Replicated game-phase state needed when a city template is retargeted to an island hunt."""

ON_HUNT = 2  # EArchonInProgressGameSubState::OnHunt (Archon_structs.hpp)


def enter_island_hunt(entries):
    """Make the replay-derived game/player state describe an active island hunt.

    Retargeting the map and GameModeClass does not run the native match transition. The
    stock Ramsgate template therefore still says InCity and its PlayerState is not active
    gameplay, which keeps the client on the city HUD/equipment path.
    """
    game_state = None
    player_state = None
    for entry in entries:
        for block in entry.parsed.blocks:
            if block.values is None:
                continue
            if block.cls_name == 'BPGS_Archon_GameState_C':
                game_state = block
            elif block.cls_name == 'player_state_bp_C':
                player_state = block

    if game_state is None or player_state is None:
        missing = []
        if game_state is None:
            missing.append('BPGS_Archon_GameState_C')
        if player_state is None:
            missing.append('player_state_bp_C')
        raise LookupError('island gameplay state missing ' + ', '.join(missing))

    previous_substate = game_state.values.get('InProgressGameSubState')
    previous_active = player_state.values.get('bIsActiveGameplay', False)
    start_time = float(game_state.values.get('ReplicatedWorldTimeSeconds', 0.0))

    game_state.values['InProgressGameSubState'] = ON_HUNT
    game_state.values['IslandGameplayStartTime'] = start_time
    player_state.values['bIsActiveGameplay'] = True

    return previous_substate, previous_active, start_time
