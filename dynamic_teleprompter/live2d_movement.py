"""Frame-rate-independent camera-free Live2D movement and easing.

Runtime state keys start with '_' and are intentionally not saved in project files.
"""
import math

DEFAULT_MOVEMENT = {
    'enabled': False,
    'model_mode': 'head',
    'direction_models': {},
    'release_model': 'default',
    'horizontal_speed': 30,
    'vertical_speed': 20,
    'turn_seconds': 350,
    'vertical_seconds': 350,
    'head_strength': 65,
    'far_y': -35,
    'near_y': 35,
    'far_scale': 40,
    'near_scale': 100,
}


def _approach(value, target, dt, duration):
    """Exponential interpolation stable across varying frame rates."""
    alpha = 1.0 - math.exp(-4.0 * dt / max(0.05, duration))
    return value + (target - value) * alpha


def _ease(value):
    value = max(0., min(1., abs(value)))
    return value * value * (3 - 2 * value)


def step_motion(layer, keys, dt):
    """Update movement for the active character; return True if visual state changed.

    X movement speed follows head-turn amplitude. Upon release, both slow to zero
    together. W/S use the same eased velocity profile but do not rotate the head.
    """
    settings = {**DEFAULT_MOVEMENT, **layer.get('movement', {})}
    if not settings['enabled'] or dt <= 0:
        return False
    x_direction = int('d' in keys) - int('a' in keys)
    y_direction = int('s' in keys) - int('w' in keys)
    # Normalize diagonals so W+D isn't faster than D alone.
    divisor = math.sqrt(2) if x_direction and y_direction else 1.
    x_direction /= divisor
    y_direction /= divisor
    turn = float(layer.get('_movement_turn', 0.))
    vertical = float(layer.get('_movement_vertical', 0.))
    new_turn = _approach(turn, x_direction, dt, float(settings['turn_seconds']) / 1000.)
    new_vertical = _approach(vertical, y_direction, dt, float(settings['vertical_seconds']) / 1000.)
    # Deceleration matches head return; retained velocity creates a small coast.
    velocity_x = math.copysign(_ease(new_turn), new_turn) * float(settings['horizontal_speed'])
    velocity_y = math.copysign(_ease(new_vertical), new_vertical) * float(settings['vertical_speed'])
    old_x, old_y = float(layer.get('x', 0)), float(layer.get('y', 0))
    far, near = float(settings['far_y']), float(settings['near_y'])
    new_x = max(-100., min(100., old_x + velocity_x * dt))
    new_y = max(far, min(near, old_y + velocity_y * dt))
    layer['_movement_turn'] = new_turn
    layer['_movement_vertical'] = new_vertical
    layer['x'] = new_x
    layer['y'] = new_y
    return abs(new_turn - turn) > 1e-5 or abs(new_vertical - vertical) > 1e-5 or abs(new_x - old_x) > 1e-5 or abs(new_y - old_y) > 1e-5



def select_direction_model(layer, keys, order):
    """Choose a transient render path; keep the layer transform and original intact."""
    settings = layer.get('movement', {})
    previous = layer.get('_movement_model_path', '')
    if settings.get('model_mode', 'head') == 'head':
        layer.pop('_movement_model_path', None)
        return bool(previous)
    assignments = settings.get('direction_models', {})
    direction = next((key for key in reversed(order) if key in keys), None)
    if direction is None and keys:
        direction = next((key for key in ('a', 'd', 'w', 's') if key in keys), None)
    if direction is not None:
        selected = assignments.get(direction) or layer.get('path', '')
        layer['_movement_last_model'] = selected
    elif settings.get('release_model', 'default') == 'last':
        selected = layer.get('_movement_last_model') or assignments.get('default') or layer.get('path', '')
    else:
        selected = assignments.get('default') or layer.get('path', '')
    layer['_movement_model_path'] = selected
    return previous != selected
