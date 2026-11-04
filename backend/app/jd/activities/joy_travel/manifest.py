from backend.core.registry import ActivityMeta

META = ActivityMeta(
    'jd', 'joy-travel', 'JOY游记',
    'backend.app.jd.activities.joy_travel.activity:JoyTravelActivity',
    config='config.json', default=True,
)
