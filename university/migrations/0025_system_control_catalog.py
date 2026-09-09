from django.db import migrations


def seed(apps,schema_editor):
    Permission=apps.get_model('university','SystemPermission')
    catalog={'maintenance':['view','create','edit','schedule','activate','deactivate','bypass'], 'lockdown':['view','activate','deactivate','emergency','bypass'], 'messages':['view','create','edit','publish','unpublish','delete'], 'health':['view']}
    for group,actions in catalog.items():
        for action in actions:
            Permission.objects.get_or_create(code=f'control.{group}.{action}',defaults={'name':f'{action.title()} {group.title()}','module':f'System {group.title()}','description':'Explicit permission required; ordinary administrator defaults do not grant this control.'})
    Template=apps.get_model('university','MessageTemplate')
    for name,title,body in [
        ('Maintenance','Scheduled system maintenance','{{system_name}} will undergo maintenance from {{maintenance_start}} to {{maintenance_end}}.'),
        ('Registration deadline','Semester registration deadline','Registration for {{semester}} closes on {{registration_deadline}}.'),
        ('Examination notice','Examination notice','Examinations for {{semester}} begin on {{exam_start_date}}.')]:
        Template.objects.get_or_create(name=name,defaults={'title':title,'body':body})

class Migration(migrations.Migration):
    dependencies=[('university','0024_controlheartbeat_notice_departments_notice_ends_at_and_more')]
    operations=[migrations.RunPython(seed,migrations.RunPython.noop)]
