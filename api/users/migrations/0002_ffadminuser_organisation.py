# -*- coding: utf-8 -*-
# Generated by Django 1.11.13 on 2018-05-18 10:42
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('api', '0012_delete_ffadminuser'),
        ('users', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='ffadminuser',
            name='organisation',
            field=models.ForeignKey(default=1, on_delete=django.db.models.deletion.CASCADE,
                                    related_name='users', to='api.Organisation'),
            preserve_default=False,
        ),
    ]