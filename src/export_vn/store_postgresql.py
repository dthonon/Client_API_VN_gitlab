"""Methods to store Biolovision data to Postgresql database.


Methods

- store_data      - Store generic data structure to file

Properties

-

"""
import json
import logging
import queue
import threading
from datetime import datetime
from uuid import uuid4

from export_vn.store_file import StoreFile
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from pyproj import Proj, transform
from sqlalchemy import (Column, DateTime, Integer, MetaData,
                        PrimaryKeyConstraint, String, Table, create_engine,
                        func, select)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine.url import URL
from sqlalchemy.sql import and_

from . import _, __version__

logger = logging.getLogger('transfer_vn.store_postgresql')


class StorePostgresqlException(Exception):
    """An exception occurred while handling download or store. """


class ObservationItem:
    """Properties of an observation, for transmission in Queue."""

    def __init__(self, site, metadata, conn, elem, in_proj, out_proj):
        """Item elements.

        Parameters
        ----------
        site : str
            VisioNature site, for column storage.
        metadata :
            sqlalchemy metadata for observation table.
        conn :
            sqlalchemy connection to database
        elem : dict
            Single observation to process and store.
        in_proj :
            Projection used in input item.
        out_proj :
            Projection added to item.
        """
        self._site = site
        self._metadata = metadata
        self._conn = conn
        self._elem = elem
        self._in_proj = in_proj
        self._out_proj = out_proj
        return None

    @property
    def site(self):
        """Return site."""
        return self._site

    @property
    def metadata(self):
        """Return metadata."""
        return self._metadata

    @property
    def conn(self):
        """Return conn."""
        return self._conn

    @property
    def elem(self):
        """Return elem."""
        return self._elem

    @property
    def in_proj(self):
        """Return in_proj."""
        return self._in_proj

    @property
    def out_proj(self):
        """Return out_proj."""
        return self._out_proj


def store_1_observation(item):
    """Process and store a single observation.

    - find insert or update date
    - simplity data to remove redundant items: dates... (TBD)
    - add x, y transform to local coordinates
    - store json in Postgresql

    Parameters
    ----------
    item : ObservationItem
        Observation item containing all parameters.
    """
    # Insert simple sightings,
    # each row contains id, update timestamp and full json body
    elem = item.elem
    logger.debug(_('Storing observation %s to database'),
                 elem['observers'][0]['id_sighting'])
    # Find last update timestamp
    if ('update_date' in elem['observers'][0]):
        # update_date = elem['observers'][0]['update_date']['@timestamp']
        update_date = elem['observers'][0]['update_date']
    else:
        # update_date = elem['observers'][0]['insert_date']['@timestamp']
        update_date = elem['observers'][0]['insert_date']

    # Add Lambert x, y transform to local coordinates
    x, y = transform(item.in_proj, item.out_proj,
                     elem['observers'][0]['coord_lon'],
                     elem['observers'][0]['coord_lat'])
    elem['observers'][0]['coord_x_local'] = x
    elem['observers'][0]['coord_y_local'] = y

    # Store in Postgresql
    items_json = json.dumps(elem)
    metadata = item.metadata
    site = item.site
    stmt = select([metadata.c.id,
                   metadata.c.site]).\
        where(and_(metadata.c.id == elem['observers'][0]['id_sighting'],
                   metadata.c.site == site))
    result = item.conn.execute(stmt)
    row = result.fetchone()
    if row is None:
        logger.debug(_('Observation not found in database, inserting new row'))
        stmt = metadata.insert().\
            values(id=elem['observers'][0]['id_sighting'],
                   site=site,
                   update_ts=update_date,
                   item=items_json)
    else:
        logger.debug(_('Observation %s found in database, updating row'),
                     row[0])
        stmt = metadata.update().\
            where(and_(metadata.c.id == elem['observers'][0]['id_sighting'],
                       metadata.c.site == site)).\
            values(update_ts=update_date,
                   item=items_json)
    result = item.conn.execute(stmt)
    return None


def observation_worker(i, q):
    """Workers running in parallel to update database."""
    while True:
        item = q.get()
        if item is None:
            break
        store_1_observation(item)
        q.task_done()
    return None


class PostgresqlUtils:
    """Provides create and delete Postgresql database method."""

    def __init__(self, config):
        self._config = config
        self._num_worker_threads = 4

    # ----------------
    # Internal methods
    # ----------------
    def _create_table(self, name, *cols):
        """Check if table exists, and create it if not

        Parameters
        ----------
        name : str
            Table name.
        cols : list
            Data returned from API call.

        """
        if (self._config.db_schema_import + '.' +
                name) not in self._metadata.tables:
            logger.info(_('Table %s not found => Creating it'), name)
            table = Table(name, self._metadata, *cols)
            table.create(self._db)
        else:
            logger.info(_('Table %s already exists => Keeping it'), name)
        return None

    def _create_download_log(self):
        """Create download_log table if it does not exist."""
        self._create_table(
            'download_log', Column('id', Integer, primary_key=True),
            Column('site', String, nullable=False, index=True),
            Column('controler', String, nullable=False),
            Column('download_ts',
                   DateTime,
                   server_default=func.now(),
                   nullable=False), Column('error_count', Integer, index=True),
            Column('http_status', Integer, index=True),
            Column('comment', String))
        return None

    def _create_increment_log(self):
        """Create increment_log table if it does not exist."""
        self._create_table(
            'increment_log', Column('id', Integer, primary_key=True),
            Column('site', String, nullable=False),
            Column('taxo_group', Integer, nullable=False),
            Column('last_ts',
                   DateTime,
                   server_default=func.now(),
                   nullable=False))
        return None

    def _create_entities_json(self):
        """Create entities_json table if it does not exist."""
        self._create_table(
            'entities_json', Column('id', Integer, nullable=False),
            Column('site', String, nullable=False),
            Column('item', JSONB, nullable=False),
            PrimaryKeyConstraint('id', 'site', name='entities_json_pk'))
        return None

    def _create_fields_json(self):
        """Create fields_json table if it does not exist."""
        self._create_table(
            'fields_json', Column('id', Integer, nullable=False),
            Column('site', String, nullable=False),
            Column('item', JSONB, nullable=False),
            PrimaryKeyConstraint('id', 'site', name='fields_json_pk'))
        return None

    def _create_forms_json(self):
        """Create forms_json table if it does not exist."""
        self._create_table(
            'forms_json', Column('id', Integer, nullable=False),
            Column('site', String, nullable=False),
            Column('item', JSONB, nullable=False),
            PrimaryKeyConstraint('id', 'site', name='forms_json_pk'))
        return None

    def _create_local_admin_units_json(self):
        """Create local_admin_units_json table if it does not exist."""
        self._create_table(
            'local_admin_units_json', Column('id', Integer, nullable=False),
            Column('site', String, nullable=False),
            Column('item', JSONB, nullable=False),
            PrimaryKeyConstraint('id',
                                 'site',
                                 name='local_admin_units_json_pk'))
        return None

    def _create_uuid_xref(self):
        """Create uuid_xref table if it does not exist."""
        self._create_table(
            'uuid_xref', Column('id', Integer, nullable=False),
            Column('site', String, nullable=False),
            Column('universal_id', String, nullable=False),
            Column('uuid', String, nullable=False),
            Column('alias', JSONB, nullable=True),
            Column('update_ts', DateTime,
                   server_default=func.now(),
                   nullable=False),
            PrimaryKeyConstraint('id', 'site', name='uuid_xref_json_pk'))
        return None

    def _create_observations_json(self):
        """Create observations_json table if it does not exist."""
        self._create_table(
            'observations_json', Column('id', Integer, nullable=False),
            Column('site', String, nullable=False),
            Column('item', JSONB, nullable=False),
            Column('update_ts', Integer, nullable=False),
            PrimaryKeyConstraint('id', 'site', name='observations_json_pk'))
        return None

    def _create_observers_json(self):
        """Create observers_json table if it does not exist."""
        self._create_table(
            'observers_json', Column('id', Integer, nullable=False),
            Column('site', String, nullable=False),
            Column('item', JSONB, nullable=False),
            PrimaryKeyConstraint('id', 'site', name='observers_json_pk'))
        return None

    def _create_places_json(self):
        """Create places_json table if it does not exist."""
        self._create_table(
            'places_json', Column('id', Integer, nullable=False),
            Column('site', String, nullable=False),
            Column('item', JSONB, nullable=False),
            PrimaryKeyConstraint('id', 'site', name='places_json_pk'))
        return None

    def _create_species_json(self):
        """Create species_json table if it does not exist."""
        self._create_table(
            'species_json', Column('id', Integer, nullable=False),
            Column('site', String, nullable=False),
            Column('item', JSONB, nullable=False),
            PrimaryKeyConstraint('id', 'site', name='species_json_pk'))
        return None

    def _create_taxo_groups_json(self):
        """Create taxo_groups_json table if it does not exist."""
        self._create_table(
            'taxo_groups_json', Column('id', Integer, nullable=False),
            Column('site', String, nullable=False),
            Column('item', JSONB, nullable=False),
            PrimaryKeyConstraint('id', 'site', name='taxo_groups_json_pk'))
        return None

    def _create_territorial_units_json(self):
        """Create territorial_units_json table if it does not exist."""
        self._create_table(
            'territorial_units_json', Column('id', Integer, nullable=False),
            Column('site', String, nullable=False),
            Column('item', JSONB, nullable=False),
            PrimaryKeyConstraint('id',
                                 'site',
                                 name='territorial_units_json_pk'))
        return None

    # ---------------
    # External methods
    # ---------------
    def create_database(self):
        """Create database, roles..."""
        # Connect first using postgres database, that always exists
        logger.info(
            _('Connecting to postgres database, to create %s database'),
            self._config.db_name)
        db_url = {
            'drivername': 'postgresql+psycopg2',
            'username': self._config.db_user,
            'password': self._config.db_pw,
            'host': self._config.db_host,
            'port': self._config.db_port,
            'database': 'postgres'
        }
        db = create_engine(URL(**db_url), echo=False)
        conn = db.connect()
        conn.connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

        # Group role:
        text = """
            SELECT FROM pg_catalog.pg_roles WHERE rolname = '{db_group}'
            """.format(db_group=self._config.db_group)
        result = conn.execute(text)
        row = result.fetchone()
        if row is None:
            text = """
                CREATE ROLE {db_group} NOLOGIN NOSUPERUSER INHERIT NOCREATEDB NOCREATEROLE NOREPLICATION
                """.format(db_group=self._config.db_group)
        else:
            text = """
                ALTER ROLE {db_group} NOLOGIN NOSUPERUSER INHERIT NOCREATEDB NOCREATEROLE NOREPLICATION
                """.format(db_group=self._config.db_group)
        conn.execute(text)

        # Import role:
        text = 'GRANT {} TO {}'.format(self._config.db_group,
                                       self._config.db_user)
        conn.execute(text)

        # Create database:
        text = 'CREATE DATABASE {} WITH OWNER = {}'.format(
            self._config.db_name, self._config.db_group)
        conn.execute(text)
        text = 'GRANT ALL ON DATABASE {} TO {}'.format(self._config.db_name,
                                                       self._config.db_group)
        conn.execute(text)
        conn.close()
        db.dispose()

        return None

    def drop_database(self):
        """Force session deconnection and drop database, roles..."""
        logger.info(
            _('Connecting to postgres database, to delete %s database'),
            self._config.db_name)
        db_url = {
            'drivername': 'postgresql+psycopg2',
            'username': self._config.db_user,
            'password': self._config.db_pw,
            'host': self._config.db_host,
            'port': self._config.db_port,
            'database': 'postgres'
        }
        db = create_engine(URL(**db_url), echo=False)
        conn = db.connect()
        conn.connection.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)

        version = conn.dialect.server_version_info
        pid_column = ('pid' if (version >= (9, 2)) else 'procpid')

        text = '''
        SELECT pg_terminate_backend(pg_stat_activity.%(pid_column)s)
        FROM pg_stat_activity
        WHERE pg_stat_activity.datname = '%(database)s'
          AND %(pid_column)s <> pg_backend_pid();
        ''' % {
            'pid_column': pid_column,
            'database': self._config.db_name
        }
        conn.execute(text)
        text = 'DROP DATABASE IF EXISTS {}'.format(self._config.db_name)
        conn.execute(text)
        text = 'DROP ROLE IF EXISTS {}'.format(self._config.db_group)
        conn.execute(text)

        conn.close()
        db.dispose()
        return None

    def create_json_tables(self):
        """Create all internal and jsonb tables."""
        # Initialize interface to Postgresql DB
        db_url = {
            'drivername': 'postgresql+psycopg2',
            'username': self._config.db_user,
            'password': self._config.db_pw,
            'host': self._config.db_host,
            'port': self._config.db_port,
            'database': self._config.db_name
        }

        # Connect to database
        logger.info(_('Connecting to %s database, to finalize creation'),
                    self._config.db_name)
        self._db = create_engine(URL(**db_url), echo=False)
        conn = self._db.connect()

        # Add extensions
        text = 'CREATE EXTENSION IF NOT EXISTS pgcrypto'
        conn.execute(text)
        text = 'CREATE EXTENSION IF NOT EXISTS adminpack'
        conn.execute(text)
        text = 'CREATE EXTENSION IF NOT EXISTS "uuid-ossp"'
        conn.execute(text)
        text = 'CREATE EXTENSION IF NOT EXISTS postgis'
        conn.execute(text)
        text = 'CREATE EXTENSION IF NOT EXISTS postgis_topology'
        conn.execute(text)

        # Create import schema
        text = 'CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}'.format(
            self._config.db_schema_import, self._config.db_group)
        conn.execute(text)

        # Enable privileges
        text = '''
        ALTER DEFAULT PRIVILEGES
           GRANT INSERT, SELECT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES
           TO postgres'''
        conn.execute(text)
        text = '''
        ALTER DEFAULT PRIVILEGES
           GRANT INSERT, SELECT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER ON TABLES
           TO {}'''.format(self._config.db_group)
        conn.execute(text)

        # Set path to include VN import schema
        dbschema = self._config.db_schema_import
        self._metadata = MetaData(schema=dbschema)
        self._metadata.reflect(self._db)

        # Check if tables exist or else create them
        self._create_download_log()
        self._create_increment_log()

        self._create_entities_json()
        self._create_fields_json()
        self._create_forms_json()
        self._create_local_admin_units_json()
        self._create_uuid_xref()
        self._create_observations_json()
        self._create_observers_json()
        self._create_places_json()
        self._create_species_json()
        self._create_taxo_groups_json()
        self._create_territorial_units_json()

        conn.close()
        self._db.dispose()

        return None

    def count_json_obs(self):
        """Count observations stored in json table, by site and taxonomy.

        Returns
        -------
        dict
            Count of observations by site and taxonomy.
        """
        logger.info('Counting observations in database for all sites')
        db_url = {
            'drivername': 'postgresql+psycopg2',
            'username': self._config.db_user,
            'password': self._config.db_pw,
            'host': self._config.db_host,
            'port': self._config.db_port,
            'database': self._config.db_name
        }

        # Connect and set path to include VN import schema
        logger.info(_('Connecting to database %s'), self._config.db_name)
        self._db = create_engine(URL(**db_url), echo=False)
        conn = self._db.connect()
        dbschema = self._config.db_schema_import
        self._metadata = MetaData(schema=dbschema)
        # self._metadata.reflect(self._db)

        text = '''
        SELECT site, ((item->>0)::json->'species') ->> 'taxonomy' AS taxonomy, COUNT(id)
              FROM {}.observations_json
              GROUP BY site, ((item->>0)::json->'species') ->> 'taxonomy';
        '''.format(dbschema)

        result = conn.execute(text).fetchall()

        return result

    def count_col_obs(self):
        """Count observations stored in column table, by site and taxonomy.

        Returns
        -------
        dict
            Count of observations by site and taxonomy.
        """
        logger.info(_('Counting observations in database for all sites'))
        db_url = {
            'drivername': 'postgresql+psycopg2',
            'username': self._config.db_user,
            'password': self._config.db_pw,
            'host': self._config.db_host,
            'port': self._config.db_port,
            'database': self._config.db_name
        }

        # Connect and set path to include VN import schema
        logger.info(_('Connecting to database %s'), self._config.db_name)
        self._db = create_engine(URL(**db_url), echo=False)
        conn = self._db.connect()
        dbschema = self._config.db_schema_vn
        self._metadata = MetaData(schema=dbschema)
        # self._metadata.reflect(self._db)

        text = '''
        SELECT o.site, o.taxonomy, t.name, COUNT(o.id_sighting)
            FROM {}.observations AS o
                LEFT JOIN {}.taxo_groups AS t ON (o.taxonomy::integer = t.id AND o.site LIKE t.site)
            GROUP BY o.site, o.taxonomy, t.name
        '''.format(dbschema, dbschema)

        result = conn.execute(text).fetchall()

        return result


class StorePostgresql:
    """Provides store to Postgresql database method."""

    def __init__(self, config):
        self._config = config
        self._file_store = StoreFile(config)

        self._num_worker_threads = 4

        # Initialize interface to Postgresql DB
        db_url = {
            'drivername': 'postgresql+psycopg2',
            'username': self._config.db_user,
            'password': self._config.db_pw,
            'host': self._config.db_host,
            'port': self._config.db_port,
            'database': self._config.db_name
        }

        dbschema = self._config.db_schema_import
        self._metadata = MetaData(schema=dbschema)
        logger.info(_('Connecting to database %s'), self._config.db_name)

        # Connect and set path to include VN import schema
        self._db = create_engine(URL(**db_url), echo=False)
        self._conn = self._db.connect()

        # Get dbtable definition
        self._metadata.reflect(bind=self._db, schema=dbschema)

        # Map Biolovision tables in a single dict for easy reference
        self._table_defs = {
            'entities': {
                'type': 'simple',
                'metadata': None
            },
            'fields': {
                'type': 'simple',
                'metadata': None
            },
            'forms': {
                'type': 'others',
                'metadata': None
            },
            'local_admin_units': {
                'type': 'geometry',
                'metadata': None
            },
            'uuid_xref': {
                'type': 'others',
                'metadata': None
            },
            'observations': {
                'type': 'observation',
                'metadata': None
            },
            'observers': {
                'type': 'simple',
                'metadata': None
            },
            'places': {
                'type': 'geometry',
                'metadata': None
            },
            'species': {
                'type': 'simple',
                'metadata': None
            },
            'taxo_groups': {
                'type': 'simple',
                'metadata': None
            },
            'territorial_units': {
                'type': 'simple',
                'metadata': None
            }
        }
        self._table_defs['entities']['metadata'] = self._metadata.tables[
            dbschema + '.entities_json']
        self._table_defs['fields']['metadata'] = self._metadata.tables[
            dbschema + '.fields_json']
        self._table_defs['forms']['metadata'] = self._metadata.tables[
            dbschema + '.forms_json']
        self._table_defs['local_admin_units'][
            'metadata'] = self._metadata.tables[dbschema +
                                                '.local_admin_units_json']
        self._table_defs['uuid_xref']['metadata'] = self._metadata.tables[
            dbschema + '.uuid_xref']
        self._table_defs['observations']['metadata'] = self._metadata.tables[
            dbschema + '.observations_json']
        self._table_defs['observers']['metadata'] = self._metadata.tables[
            dbschema + '.observers_json']
        self._table_defs['places']['metadata'] = self._metadata.tables[
            dbschema + '.places_json']
        self._table_defs['species']['metadata'] = self._metadata.tables[
            dbschema + '.species_json']
        self._table_defs['taxo_groups']['metadata'] = self._metadata.tables[
            dbschema + '.taxo_groups_json']
        self._table_defs['territorial_units'][
            'metadata'] = self._metadata.tables[dbschema +
                                                '.territorial_units_json']

        # Create parallel workers for database queries
        self._observations_queue = queue.Queue(maxsize=100000)
        self._observations_threads = []
        for i in range(self._num_worker_threads):
            t = threading.Thread(target=observation_worker,
                                 args=(i, self._observations_queue))
            t.start()
            self._observations_threads.append(t)

        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        """Send finish signal to workers and wait for tasks to finish."""
        for i in range(self._num_worker_threads):
            self._observations_queue.put(None)
        for t in self._observations_threads:
            t.join()
        self._conn.close()

    @property
    def version(self):
        """Return version."""
        return __version__

    # ----------------
    # Internal methods
    # ----------------
    def _store_simple(self, controler, items_dict):
        """Write items_dict to database.

        Converts each element to JSON and store to database in a tables
        named from controler.

        Parameters
        ----------
        controler : str
            Name of API controler.
        items_dict : dict
            Data returned from API call.

        Returns
        -------
        int
            Count of items stored (not exact for observations, due to forms).
        """

        # Loop on data array to store each element to database
        logger.info(_('Storing %d items from %s of site %s'),
                    len(items_dict['data']), controler, self._config.site)
        # trans = self._conn.begin()
        try:
            for elem in items_dict['data']:
                # Convert to json
                items_json = json.dumps(elem)
                logger.debug(_('Storing element %s'), items_json)
                stmt = select([self._table_defs[controler]['metadata'].c.id,
                               self._table_defs[controler]['metadata'].c.site]).\
                    where(and_(self._table_defs[controler]['metadata'].c.id == elem['id'],
                               self._table_defs[controler]['metadata'].c.site == self._config.site))
                result = self._conn.execute(stmt)
                row = result.fetchone()
                if row is None:
                    logger.debug(
                        _('Element not found in database, inserting new row'))
                    stmt = self._table_defs[controler]['metadata'].insert().\
                        values(id=elem['id'],
                               site=self._config.site,
                               item=items_json)
                else:
                    logger.debug(
                        _('Element %s found in database, updating row'),
                        row[0])
                    stmt = self._table_defs[controler]['metadata'].update().\
                        where(and_(self._table_defs[controler]['metadata'].c.id == elem['id'],
                                   self._table_defs[controler]['metadata'].c.site == self._config.site)).\
                        values(id=elem['id'],
                               site=self._config.site,
                               item=items_json)
                result = self._conn.execute(stmt)
            # trans.commit()
        except:
            # trans.rollback()
            raise

        return len(items_dict['data'])

    def _store_geometry(self, controler, items_dict):
        """Add local coordinates and pass to _store_simple.

        Add X, Y local coordinates to each item and then
        send to _store_simple for database storage

        Parameters
        ----------
        controler : str
            Name of API controler.
        items_dict : dict
            Data returned from API call.

        Returns
        -------
        int
            Count of items stored (not exact for observations, due to forms).
        """

        in_proj = Proj(init='epsg:4326')
        out_proj = Proj(init='epsg:' + self._config.db_out_proj)
        # Loop on data array to reproject
        for elem in items_dict['data']:
            elem['coord_x_local'], elem['coord_y_local'] = transform(
                in_proj, out_proj, elem['coord_lon'], elem['coord_lat'])
        return self._store_simple(controler, items_dict)

    def _store_form(self, items_dict):
        """Write forms to database.

        Check if forms is in database and either insert or update.

        Parameters
        ----------
        items_dict : dict
            Forms data to store.

        Returns
        -------
        int
            Count of items stored.
        """

        controler = 'forms'
        logger.debug(_('Storing %d items from %s of site %s'), len(items_dict),
                     controler, self._config.site)
        try:
            # Add local coordinates
            if ('lon' in items_dict) and ('lat' in items_dict):
                in_proj = Proj(init='epsg:4326')
                out_proj = Proj(init='epsg:' + self._config.db_out_proj)
                items_dict['coord_x_local'], items_dict[
                    'coord_y_local'] = transform(in_proj, out_proj,
                                                 items_dict['lon'],
                                                 items_dict['lat'])

            # Convert to json
            items_json = json.dumps(items_dict)
            logger.debug(_('Storing element %s'), items_json)
            stmt = select([self._table_defs[controler]['metadata'].c.id,
                           self._table_defs[controler]['metadata'].c.site]).\
                where(and_(self._table_defs[controler]['metadata'].c.id == items_dict['@id'],
                           self._table_defs[controler]['metadata'].c.site == self._config.site))
            result = self._conn.execute(stmt)
            row = result.fetchone()
            if row is None:
                logger.debug(
                    _('Element not found in database, inserting new row'))
                stmt = self._table_defs[controler]['metadata'].insert().\
                    values(id=items_dict['@id'],
                           site=self._config.site,
                           item=items_json)
            else:
                logger.debug(_('Element %s found in database, updating row'),
                             row[0])
                stmt = self._table_defs[controler]['metadata'].update().\
                    where(and_(self._table_defs[controler]['metadata'].c.id == items_dict['@id'],
                               self._table_defs[controler]['metadata'].c.site == self._config.site)).\
                    values(id=items_dict['@id'],
                           site=self._config.site,
                           item=items_json)
            result = self._conn.execute(stmt)
        except:
            raise

        return len(items_dict)

    def _store_uuid(self, obs_id, universal_id=''):
        """Creates UUID and store along id and site.

        If (id, site) does not exist:
        - creates an UID
        - store it, along with id, site, universal_id to table.

        Parameters
        ----------
        obs_id : str
            Observations id.
        universal_id : str
            Observations universal id.

        Returns
        -------
        int
            Count of items stored.
        """

        controler = 'uuid_xref'
        try:
            stmt = select([self._table_defs[controler]['metadata'].c.id,
                           self._table_defs[controler]['metadata'].c.site]).\
                where(and_(self._table_defs[controler]['metadata'].c.id == obs_id,
                           self._table_defs[controler]['metadata'].c.site ==
                           self._config.site))
            result = self._conn.execute(stmt)
            row = result.fetchone()
            if row is None:
                logger.debug(_('Creating UUID for observation %s site %s'),
                             obs_id, self._config.site)
                stmt = self._table_defs[controler]['metadata'].insert().\
                    values(id=obs_id,
                           site=self._config.site,
                           universal_id=universal_id,
                           uuid=uuid4(),
                           update_ts=datetime.now())
                result = self._conn.execute(stmt)
            else:
                logger.debug(_('UUID found for observation %s site %s'),
                             obs_id, self._config.site)
        except:
            raise

        return 1

    def _store_observation(self, controler, items_dict):
        """Iterate through observations or forms and store.

        Checks if sightings or forms and iterate on each sighting
        - find insert or update date
        - simplity data to remove redundant items: dates... (TBD)
        - add x, y transform to local coordinates
        - store json in Postgresql

        Parameters
        ----------
        controler : str
            Name of API controler.
        items_dict : dict
            Data returned from API call.

        Returns
        -------
        int
            Count of items stored (not exact for observations, due to forms).
        """
        # Insert simple sightings, each row contains id, update timestamp and full json body
        in_proj = Proj(init='epsg:4326')
        out_proj = Proj(init='epsg:' + self._config.db_out_proj)
        nb_obs = 0
        # trans = self._conn.begin()
        try:
            logger.debug(_('Storing %d single observations'),
                         len(items_dict['data']['sightings']))
            for i in range(0, len(items_dict['data']['sightings'])):
                elem = items_dict['data']['sightings'][i]
                # Create UUID
                self._store_uuid(elem['observers'][0]['id_sighting'],
                                 elem['observers'][0]['id_universal'])
                # Senf observation to queue
                obs = ObservationItem(self._config.site,
                                      self._table_defs[controler]['metadata'],
                                      self._conn, elem, in_proj, out_proj)
                self._observations_queue.put(obs)
                nb_obs += 1

            if ('forms' in items_dict['data']):
                for f in range(0, len(items_dict['data']['forms'])):
                    forms_data = {}
                    for (k, v) in items_dict['data']['forms'][f].items():
                        if k == 'sightings':
                            nb_s = len(v)
                            logger.debug('Storing %d observations in form %d',
                                         nb_s, f)
                            for i in range(0, nb_s):
                                obs = ObservationItem(
                                    self._config.site,
                                    self._table_defs[controler]['metadata'],
                                    self._conn, v[i], in_proj, out_proj)
                                self._observations_queue.put(obs)
                                nb_obs += 1
                        else:
                            # Put anything except sightings in forms data
                            forms_data[k] = v
                    self._store_form(forms_data)

            # Wait for threads to finish before commit
            # self._observations_queue.join()

            # trans.commit()
        except:
            # trans.rollback()
            raise

        logger.debug(_('Stored %d observations or forms to database'), nb_obs)
        return nb_obs

    # ---------------
    # External methods
    # ---------------
    def store(self, controler, seq, items_dict):
        """Write items_dict to database.

        Processing depends on controler, as items_dict structure varies.
        Converts each element to JSON and store to database in a tables
        named from controler.

        Parameters
        ----------
        controler : str
            Name of API controler.
        seq : str
            (Composed) sequence of data stream => Unused for db
        items_dict : dict
            Data returned from API call.

        Returns
        -------
        int
            Count of items stored (not exact for observations, due to forms).
        """
        self._file_store.store(controler, seq, items_dict)
        if self._table_defs[controler]['type'] == 'simple':
            nb_item = self._store_simple(controler, items_dict)
        elif self._table_defs[controler]['type'] == 'geometry':
            nb_item = self._store_geometry(controler, items_dict)
        elif self._table_defs[controler]['type'] == 'observation':
            nb_item = self._store_observation(controler, items_dict)
        else:
            raise StorePostgresqlException(_('Not implemented'))

        return nb_item

    def delete_obs(self, obs_list):
        """Delete observations stored in database.

        Parameters
        ----------
        obs_list : list
            Data returned from API call.

        Returns
        -------
        int
            Count of items deleted.
        """
        logger.info(_('Deleting %d observations from database'), len(obs_list))
        # trans = conn.begin()
        nb_delete = 0
        try:
            for obs in obs_list:
                nd = self._conn.execute(
                    self._table_defs['observations']
                    ['metadata'].delete().where(
                        and_(
                            self._table_defs['observations']['metadata'].c.id
                            == obs, self._table_defs['observations']
                            ['metadata'].c.site == self._config.site)))
                nb_delete += nd.rowcount
            # trans.commit()
        except:
            # trans.rollback()
            raise

        return nb_delete

    def log(self, site, controler, error_count=0, http_status=0, comment=''):
        """Write download log entries to database.

        Parameters
        ----------
        site : str
            VN site name.
        controler : str
            Name of API controler.
        error_count : integer
            Number of errors during download.
        http_status : integer
            HTTP status of latest download.
        comment : str
            Optional comment, in free text.
        """
        metadata = self._metadata.tables[self._config.db_schema_import + '.' +
                                         'download_log']
        stmt = metadata.insert().\
            values(site=site,
                   controler=controler,
                   error_count=error_count,
                   http_status=http_status,
                   comment=comment)
        self._conn.execute(stmt)

        return None

    def increment_log(self, site, taxo_group, last_ts):
        """Write last increment timestamp to database.

        Parameters
        ----------
        site : str
            VN site name.
        taxo_group : str
            Taxo_group updated.
        last_ts : timestamp
            Timestamp of last update of this taxo_group.
        """
        metadata = self._metadata.tables[self._config.db_schema_import + '.' +
                                         'increment_log']
        stmt = select([metadata.c.taxo_group,
                       metadata.c.site]).\
            where(and_(metadata.c.taxo_group == taxo_group,
                       metadata.c.site == site))
        result = self._conn.execute(stmt)
        row = result.fetchone()
        if row is None:
            stmt = metadata.insert().\
                values(taxo_group=taxo_group,
                       site=site,
                       last_ts=last_ts)
        else:
            stmt = metadata.update().\
                where(and_(metadata.c.taxo_group == taxo_group,
                           metadata.c.site == site)).\
                values(taxo_group=taxo_group,
                       site=site,
                       last_ts=last_ts)
        result = self._conn.execute(stmt)

        return None

    def increment_get(
            self,
            site,
            taxo_group,
    ):
        """Get last increment timestamp from database.

        Parameters
        ----------
        site : str
            VN site name.
        taxo_group : str
            Taxo_group updated.

        Returns
        -------
        timestamp
            Timestamp of last update of this taxo_group.
        """
        metadata = self._metadata.tables[self._config.db_schema_import + '.' +
                                         'increment_log']
        stmt = select([metadata.c.last_ts]).\
            where(and_(metadata.c.taxo_group == taxo_group,
                       metadata.c.site == site))
        result = self._conn.execute(stmt)
        row = result.fetchone()

        if row is None:
            return None
        else:
            return row[0]
