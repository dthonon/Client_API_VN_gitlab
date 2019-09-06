A new ``filter:`` section is added to YAML configuration file.
``taxo_exclude:`` list needs to be moved to this new section.

To limit full download to a time interval, you can add:

- ``start_date``, optional date of first observation. If omitted, start with earliest data.
- ``end_date``, optional date of last observation. If omitted, start with latest data.

Date format is YYYY-MM-DD.