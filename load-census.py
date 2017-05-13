# *********************************************************************************************************************
# load-census.py
# *********************************************************************************************************************
#
# A script for loading Australian Bureau of Statistics Census 2016 data and boundaries
#
# Author: Hugh Saalmans
# GitHub: minus34
# Twitter: @minus34
#
# Copyright:
#  - Code is licensed under an Apache License, version 2.0
#  - Data is copyright ABS - licensed under a Creative Commons (By Attribution) license.
#    See http://abs.gov.au for the correct attribution

# Process:
#   1. 
#
# *********************************************************************************************************************

import argparse
import io
import logging.config
import os
import pandas  # module needs to be installed
import psycopg2  # module needs to be installed (IMPORTANT: need to install 'xlrd' module for Pandas to read XLSX files)
import utils

from datetime import datetime


def main():
    full_start_time = datetime.now()

    # set command line arguments
    args = set_arguments()

    # get settings from arguments
    settings = get_settings(args)

    # connect to Postgres
    try:
        pg_conn = psycopg2.connect(settings['pg_connect_string'])
    except psycopg2.Error:
        logger.fatal("Unable to connect to database\nACTION: Check your Postgres parameters and/or database security")
        return False

    pg_conn.autocommit = True
    pg_cur = pg_conn.cursor()

    # add postgis to database (in the public schema) - run this in a try to confirm db user has privileges
    try:
        pg_cur.execute("SET search_path = public, pg_catalog; CREATE EXTENSION IF NOT EXISTS postgis")
    except psycopg2.Error:
        logger.fatal("Unable to add PostGIS extension\nACTION: Check your Postgres user privileges or PostGIS install")
        return False

    # test if ST_SubDivide exists (only in PostGIS 2.2+). It's used to split boundaries for faster processing
    utils.check_postgis_version(pg_cur, settings, logger)

    # START LOADING DATA

    # PART 1 - load census data from CSV files
    logger.info("")
    start_time = datetime.now()
    logger.info("Part 1 of 4 : Start census data load : {0}".format(start_time))
    # drop_tables_and_vacuum_db(pg_cur, settings)
    load_metadata(pg_cur, settings)
    create_data_tables(pg_cur, settings)
    populate_data_tables(settings)
    # index_raw_gnaf(settings)
    # if settings['primary_foreign_keys']:
    #     create_primary_foreign_keys(settings)
    # else:
    #     logger.info("\t- Step 6 of 7 : primary & foreign keys NOT created")
    # analyse_raw_gnaf_tables(pg_cur, settings)
    # # set postgres search path back to the default
    # pg_cur.execute("SET search_path = public, pg_catalog")
    # logger.info("Part 1 of 4 : Raw GNAF loaded! : {0}".format(datetime.now() - start_time))

    # PART 2 - load census boundaries from Shapefiles
    logger.info("")
    start_time = datetime.now()
    logger.info("Part 2 of 4 : Start census boundary load : {0}".format(start_time))
    load_boundaries(pg_cur, settings)
    # prep_boundaries(pg_cur, settings)
    # create_boundaries_for_analysis(settings)
    logger.info("Part 2 of 4 : Census boundaries loaded! : {0}".format(datetime.now() - start_time))

    # # PART 3 - create flattened and standardised GNAF and Administrative Boundary reference tables
    # logger.info("")
    # start_time = datetime.now()
    # logger.info("Part 3 of 4 : Start create reference tables : {0}".format(start_time))
    # create_reference_tables(pg_cur, settings)
    # logger.info("Part 3 of 4 : Reference tables created! : {0}".format(datetime.now() - start_time))
    # 
    # # PART 4 - boundary tag GNAF addresses
    # logger.info("")
    # if settings['boundary_tag']:
    #     start_time = datetime.now()
    #     logger.info("Part 4 of 4 : Start boundary tagging addresses : {0}".format(start_time))
    #     boundary_tag_gnaf(pg_cur, settings)
    #     logger.info("Part 4 of 4 : Addresses boundary tagged: {0}".format(datetime.now() - start_time))
    # else:
    #     logger.warning("Part 4 of 4 : Addresses NOT boundary tagged")
    # 
    # # # PART 5 - get record counts for QA
    # logger.info("")
    # start_time = datetime.now()
    # logger.info("Part 5 of 5 : Start row counts : {0}".format(start_time))
    # create_qa_tables(pg_cur, settings)
    # logger.info("Part 5 of 5 : Got row counts : {0}".format(datetime.now() - start_time))

    # close Postgres connection
    pg_cur.close()
    pg_conn.close()

    logger.info("")
    logger.info("Total time : : {0}".format(datetime.now() - full_start_time))

    return True


# set the command line arguments for the script
def set_arguments():

    parser = argparse.ArgumentParser(
        description='A quick way to load the complete GNAF and PSMA Admin Boundaries into Postgres, '
                    'simplified and ready to use as reference data for geocoding, analysis and visualisation.')

    parser.add_argument(
        '--max-processes', type=int, default=3,
        help='Maximum number of parallel processes to use for the data load. (Set it to the number of cores on the '
             'Postgres server minus 2, limit to 12 if 16+ cores - there is minimal benefit beyond 12). Defaults to 6.')

    # PG Options
    parser.add_argument(
        '--pghost',
        help='Host name for Postgres server. Defaults to PGHOST environment variable if set, otherwise localhost.')
    parser.add_argument(
        '--pgport', type=int,
        help='Port number for Postgres server. Defaults to PGPORT environment variable if set, otherwise 5432.')
    parser.add_argument(
        '--pgdb',
        help='Database name for Postgres server. Defaults to PGDATABASE environment variable if set, '
             'otherwise utils.')
    parser.add_argument(
        '--pguser',
        help='Username for Postgres server. Defaults to PGUSER environment variable if set, otherwise postgres.')
    parser.add_argument(
        '--pgpassword',
        help='Password for Postgres server. Defaults to PGPASSWORD environment variable if set, '
             'otherwise \'password\'.')

    # schema names for the census data & boundary tables
    census_year = '2016'
    
    parser.add_argument(
        '--census-year', default=census_year,
        help='Census year as YYYY. Defaults to last census \'' + census_year + '\'.')
    parser.add_argument(
        '--data-schema', default='census_' + census_year + '_data',
        help='Schema name to store raw GNAF tables in. Defaults to \'census_' + census_year + '\'.')
    parser.add_argument(
        '--boundary-schema', default='census_' + census_year + '_bdys',
        help='Schema name to store raw admin boundary tables in. Defaults to \'census_' + census_year + '\'.')

    # directories
    parser.add_argument(
        '--census-data-path', required=True,
        help='Path to source census data tables (*.csv files). '
             'This directory must be accessible by the Postgres server, and the local path to the directory for the '
             'server must be set via the local-server-dir argument if it differs from this path.')
    parser.add_argument(
        '--local-server-dir',
        help='Local path on server corresponding to census-data-path, if different to census-data-path.')
    parser.add_argument(
        '--census-bdys-path', required=True, help='Local path to source admin boundary files.')

    # # states to load
    # parser.add_argument('--states', nargs='+', choices=["ACT", "NSW", "NT", "OT", "QLD", "SA", "TAS", "VIC", "WA"],
    #                     default=["ACT", "NSW", "NT", "OT", "QLD", "SA", "TAS", "VIC", "WA"],
    #                     help='List of states to load data for. Defaults to all states.')

    return parser.parse_args()


# create the dictionary of settings
def get_settings(args):
    settings = dict()

    settings['max_concurrent_processes'] = args.max_processes
    settings['census_year'] = args.census_year
    # settings['states_to_load'] = args.states
    settings['states'] = ["ACT", "NSW", "NT", "OT", "QLD", "SA", "TAS", "VIC", "WA"]
    settings['data_schema'] = args.data_schema
    settings['boundary_schema'] = args.boundary_schema
    settings['data_network_directory'] = args.census_data_path.replace("\\", "/")

    if args.local_server_dir:
        settings['data_pg_server_local_directory'] = args.local_server_dir.replace("\\", "/")
    else:
        settings['data_pg_server_local_directory'] = settings['data_network_directory']
    settings['boundaries_local_directory'] = args.census_bdys_path.replace("\\", "/")

    # create postgres connect string
    settings['pg_host'] = args.pghost or os.getenv("PGHOST", "localhost")
    settings['pg_port'] = args.pgport or os.getenv("PGPORT", 5432)
    settings['pg_db'] = args.pgdb or os.getenv("PGDATABASE", "geo")
    settings['pg_user'] = args.pguser or os.getenv("PGUSER", "postgres")
    settings['pg_password'] = args.pgpassword or os.getenv("PGPASSWORD", "password")

    settings['pg_connect_string'] = "dbname='{0}' host='{1}' port='{2}' user='{3}' password='{4}'".format(
        settings['pg_db'], settings['pg_host'], settings['pg_port'], settings['pg_user'], settings['pg_password'])

    # set postgres script directory
    settings['sql_dir'] = os.path.join(os.path.dirname(os.path.realpath(__file__)), "postgres-scripts")

    # # set the list of admin bdys to create analysis tables for and to boundary tag with
    # admin_bdy_list = list()
    # admin_bdy_list.append(["state_bdys", "state_pid"])
    # admin_bdy_list.append(["locality_bdys", "locality_pid"])
    #
    # # only process bdys if states to load have them
    # if settings['states_to_load'] != ['OT']:
    #     admin_bdy_list.append(["commonwealth_electorates", "ce_pid"])
    # if settings['states_to_load'] != ['ACT']:
    #     admin_bdy_list.append(["local_government_areas", "lga_pid"])
    # if 'NT' in settings['states_to_load'] or 'SA' in settings['states_to_load'] \
    #         or 'VIC' in settings['states_to_load'] or 'WA' in settings['states_to_load']:
    #     admin_bdy_list.append(["local_government_wards", "ward_pid"])
    # if settings['states_to_load'] != ['OT']:
    #     admin_bdy_list.append(["state_lower_house_electorates", "se_lower_pid"])
    # if 'TAS' in settings['states_to_load'] or 'VIC' in settings['states_to_load'] or
    #     'WA' in settings['states_to_load']:
    #     admin_bdy_list.append(["state_upper_house_electorates", "se_upper_pid"])
    # settings['admin_bdy_list'] = admin_bdy_list

    return settings


def load_metadata(pg_cur, settings):
    # Step 1 of 2 : create metadata tables from Census Excel spreadsheets
    start_time = datetime.now()

    # create schema and set as search path
    if settings['data_schema'] != "public":
        pg_cur.execute("CREATE SCHEMA IF NOT EXISTS {0} AUTHORIZATION {1}"
                       .format(settings['data_schema'], settings['pg_user']))
        pg_cur.execute("SET search_path = {0}".format(settings['data_schema'],))

    # create metadata tables
    sql = "DROP TABLE IF EXISTS {0}.metadata_tables;" \
          "CREATE TABLE {0}.metadata_tables (table_number text, table_name text, table_description text) " \
          "WITH (OIDS=FALSE);" \
          "ALTER TABLE {0}.metadata_tables OWNER TO {1}".format(settings['data_schema'], settings['pg_user'])
    pg_cur.execute(sql)

    sql = "DROP TABLE IF EXISTS {0}.metadata_cells;" \
          "CREATE TABLE {0}.metadata_cells (sequential text, short_description text, long_description text, " \
          "datapack_file text, profile_table text, column_heading_description_in_profile_text text) " \
          "WITH (OIDS=FALSE);" \
          "ALTER TABLE {0}.metadata_cells OWNER TO {1}".format(settings['data_schema'], settings['pg_user'])
    pg_cur.execute(sql)

    census_metadata_dicts = [{"table": "metadata_tables", "first_row": "table number"},
                             {"table": "metadata_cells", "first_row": "sequential"}]
    # census_metadata_dicts = [{"table": "metadata_tables", "first_row": "table number", "primary_key": "table_number"},
    #                          {"table": "metadata_cells", "first_row": "sequential", "primary_key": "sequential"}]

    # get a dictionary of all files matching the metadata filename prefix
    for root, dirs, files in os.walk(settings['data_network_directory']):
        for file_name in files:
            if file_name.lower().startswith("sample_metadata_"):
                if file_name.lower().endswith(".xls"):
                    file_path = os.path.join(root, file_name)\

                    # read in excel worksheets into pandas dataframes
                    xl = pandas.ExcelFile(file_path)

                    sheets = xl.sheet_names
                    i = 0

                    for table_dict in census_metadata_dicts:

                        df = xl.parse(sheets[i])

                        # drop unwanted rows at the top
                        j = 0
                        first_row = False

                        while not first_row:
                            cell = df.iloc[j, 0]

                            if str(cell).lower() == table_dict["first_row"]:
                                df_clean = df.drop(df.index[0:j+1])
                                first_row = True

                                # drop excess columns in unclean spreadsheets
                                if table_dict["table"] == "metadata_cells":
                                    try:
                                        df_clean.drop(df.columns[[6, 7, 8]], axis=1, inplace=True)
                                    except:
                                        pass

                                # export to in-memory tab delimited text file
                                tsv_file = io.StringIO()
                                df_clean.to_csv(tsv_file, sep="\t", index=False, header=False)
                                tsv_file.seek(0)  # move position back to beginning of file before reading
                                pg_cur.copy_from(tsv_file, "{0}.{1}"
                                                 .format(settings['data_schema'], table_dict["table"]),
                                                 sep="\t", null="")

                            j += 1

                        i += 1

                    logger.info("\t\t- imported {0}".format(file_name))

    # clean up invalid rows
    pg_cur.execute("DELETE FROM {0}.metadata_tables WHERE table_number IS NULL".format(settings['data_schema']))

    # add primary keys
    pg_cur.execute("ALTER TABLE {0}.metadata_tables ADD CONSTRAINT metadata_tables_pkey PRIMARY KEY (table_number)"
                   .format(settings['data_schema']))
    pg_cur.execute("ALTER TABLE {0}.metadata_cells ADD CONSTRAINT metadata_cells_pkey PRIMARY KEY (sequential)"
                   .format(settings['data_schema']))

    # cluster tables on primary key (for minor performance improvement)
    pg_cur.execute("ALTER TABLE {0}.metadata_tables CLUSTER ON metadata_tables_pkey".format(settings['data_schema']))
    pg_cur.execute("ALTER TABLE {0}.metadata_cells CLUSTER ON metadata_cells_pkey".format(settings['data_schema']))

    # add cell type field to cells table
    pg_cur.execute("ALTER TABLE {0}.metadata_cells ADD COLUMN cell_type text".format(settings['data_schema']))

    # populate cell type field
    pg_cur.execute("UPDATE {0}.metadata_cells "
                   "SET cell_type = 'float' "
                   "WHERE lower(long_description) like '%median%' OR lower(long_description) like '%average%'"
                   .format(settings['data_schema']))

    # update stats
    pg_cur.execute("ANALYZE {0}.metadata_tables".format(settings['data_schema']))
    pg_cur.execute("ANALYZE {0}.metadata_cells".format(settings['data_schema']))

    logger.info("\t- Step 1 of 2 : metadata tables created : {0}".format(datetime.now() - start_time))


def create_data_tables(pg_cur, settings):
    # Step 1 of 2 : create tables from metadata in Census Excel spreadsheets
    start_time = datetime.now()

    # get_metadata_files("metadata", settings)
    #
    # # read in excel file into a dataframe
    # df = pandas.read_excel(settings["metadata_xls"], sheetname="Sheet1")


    # prep create table sql scripts (note: file doesn't contain any schema prefixes on table names)
    sql = open(os.path.join(settings['sql_dir'], "01-03-raw-gnaf-create-tables.sql"), "r").read()

    # create schema and set as search path
    if settings['data_schema'] != "public":
        pg_cur.execute("CREATE SCHEMA IF NOT EXISTS {0} AUTHORIZATION {1}"
                       .format(settings['data_schema'], settings['pg_user']))
        pg_cur.execute("SET search_path = {0}".format(settings['data_schema'],))

        # alter create table script to run on chosen schema
        sql = sql.replace("SET search_path = public", "SET search_path = {0}".format(settings['data_schema'],))

    # set tables to unlogged to speed up the load? (if requested)
    # -- they'll have to be rebuilt using this script again after a system crash --
    if settings['unlogged_tables']:
        sql = sql.replace("CREATE TABLE ", "CREATE UNLOGGED TABLE ")
        unlogged_string = "UNLOGGED "
    else:
        unlogged_string = ""

    # create raw gnaf tables
    pg_cur.execute(sql)

    logger.info("\t- Step 3 of 7 : {1}tables created : {0}".format(datetime.now() - start_time, unlogged_string))


# load raw gnaf authority & state tables using multiprocessing
def populate_data_tables(settings):
    # Step 4 of 7 : load raw gnaf authority & state tables
    start_time = datetime.now()

    # authority code file list
    sql_list = get_data_files("authority_code", settings)

    # add state file lists
    for state in settings['states_to_load']:
        logger.info("\t\t- Loading state {}".format(state))
        sql_list.extend(get_data_files(state, settings))

    # are there any files to load?
    if len(sql_list) == 0:
        logger.fatal("No raw GNAF PSV files found\nACTION: Check your 'gnaf_network_directory' path")
        logger.fatal("\t- Step 4 of 7 : table populate FAILED!")
    else:
        # load all PSV files using multiprocessing
        utils.multiprocess_list("sql", sql_list, settings, logger)
        logger.info("\t- Step 4 of 7 : tables populated : {0}".format(datetime.now() - start_time))





def get_data_files(prefix, settings):
    sql_list = []
    prefix = prefix.lower()
    # get a dictionary of all files matching the filename prefix
    for root, dirs, files in os.walk(settings['data_network_directory']):
        for file_name in files:
            if file_name.lower().startswith(prefix + "_"):
                if file_name.lower().endswith(".psv"):
                    file_path = os.path.join(root, file_name)\
                        .replace(settings['data_network_directory'], settings['data_pg_server_local_directory'])
                    table = file_name.lower().replace(prefix + "_", "", 1).replace("_psv.psv", "")

                    # if a non-Windows Postgres server OS - fix file path
                    if settings['data_pg_server_local_directory'][0:1] == "/":
                        file_path = file_path.replace("\\", "/")
                        # logger.info(file_path

                    sql = "COPY {0}.{1} FROM '{2}' DELIMITER '|' CSV HEADER;"\
                        .format(settings['data_schema'], table, file_path)

                    sql_list.append(sql)

    return sql_list


# index raw gnaf using multiprocessing
def index_data(settings):
    # Step 5 of 7 : create indexes
    start_time = datetime.now()

    raw_sql_list = utils.open_sql_file("01-05-raw-gnaf-create-indexes.sql", settings).split("\n")
    sql_list = []
    for sql in raw_sql_list:
        if sql[0:2] != "--" and sql[0:2] != "":
            sql_list.append(sql)

    utils.multiprocess_list("sql", sql_list, settings, logger)
    logger.info("\t- Step 5 of 7 : indexes created: {0}".format(datetime.now() - start_time))


# create raw gnaf primary & foreign keys (for data integrity) using multiprocessing
def create_primary_foreign_keys(settings):
    start_time = datetime.now()

    key_sql = open(os.path.join(settings['sql_dir'], "01-06-raw-gnaf-create-primary-foreign-keys.sql"), "r").read()
    key_sql_list = key_sql.split("--")
    sql_list = []

    for sql in key_sql_list:
        sql = sql.strip()
        if sql[0:6] == "ALTER ":
            # add schema to tables names, in case raw gnaf schema not the default
            sql = sql.replace("ALTER TABLE ONLY ", "ALTER TABLE ONLY " + settings['data_schema'] + ".")
            sql_list.append(sql)

    sql_list = []

    # run queries in separate processes
    utils.multiprocess_list("sql", sql_list, settings, logger)

    logger.info("\t- Step 6 of 7 : primary & foreign keys created : {0}".format(datetime.now() - start_time))


# analyse raw GNAF tables that have not stats - need actual row counts for QA at the end
def analyse_data_tables(pg_cur, settings):
    start_time = datetime.now()
    
    # get list of tables that haven't been analysed (i.e. that have no real row count)
    sql = "SELECT nspname|| '.' || relname AS table_name " \
          "FROM pg_class C LEFT JOIN pg_namespace N ON (N.oid = C.relnamespace)" \
          "WHERE nspname = '{0}' AND relkind='r' AND reltuples = 0".format(settings['data_schema'])
    pg_cur.execute(sql)

    sql_list = []

    for pg_row in pg_cur:
        sql_list.append("ANALYZE {0}".format(pg_row[0]))

    # run queries in separate processes
    utils.multiprocess_list("sql", sql_list, settings, logger)

    logger.info("\t- Step 7 of 7 : tables analysed : {0}".format(datetime.now() - start_time))
    

# loads the admin bdy shapefiles using the shp2pgsql command line tool (part of PostGIS), using multiprocessing
def load_boundaries(pg_cur, settings):
    start_time = datetime.now()

    # drop existing views
    # pg_cur.execute(utils.open_sql_file("02-01-drop-admin-bdy-views.sql", settings))

    # create schema
    if settings['boundary_schema'] != "public":
        pg_cur.execute("CREATE SCHEMA IF NOT EXISTS {0} AUTHORIZATION {1}"
                       .format(settings['boundary_schema'], settings['pg_user']))

    # # set psql connect string and password
    # psql_str = "psql -U {0} -d {1} -h {2} -p {3}"\
    #     .format(settings['pg_user'], settings['pg_db'], settings['pg_host'], settings['pg_port'])
    #
    # password_str = ''
    # if not os.getenv("PGPASSWORD"):
    #     if platform.system() == "Windows":
    #         password_str = "SET"
    #     else:
    #         password_str = "export"
    #
    #     password_str += " PGPASSWORD={0}&&".format(settings['pg_password'])

    # get file list
    table_list = list()
    create_list = list()
    append_list = list()

    # get a dictionary of Shapefile paths
    for root, dirs, files in os.walk(settings['boundaries_local_directory']):
        for file_name in files:
            file_name = file_name.lower()
            
            if file_name.endswith(".shp"):
                file_dict = dict()
                file_dict['file_path'] = os.path.join(root, file_name)

                if file_name.startswith("mb_"):
                    for state in settings['states']:
                        state = state.lower()
                        
                        if state in file_name:
                            file_dict['pg_table'] = file_name.replace("_" + state + ".shp", "_aust", 1)
                else:
                    file_dict['pg_table'] = file_name.replace(".shp", "")

                file_dict['pg_schema'] = settings['boundary_schema']

                # set to replace or append to table depending on whether this is the 1st state for that dataset
                # (only applies to meshblocks in Census 2016)
                table_list_add = False
    
                if file_dict['pg_table'] not in table_list:
                    table_list_add = True

                    file_dict['delete_table'] = True
                else:
                    file_dict['delete_table'] = False

                file_dict['spatial'] = True

                if table_list_add:
                    table_list.append(file_dict['pg_table'])
                    create_list.append(file_dict)
                else:
                    append_list.append(file_dict)

    # logger.info(create_list)
    # logger.info(append_list)

    # are there any files to load?
    if len(create_list) == 0:
        logger.fatal("No census boundary files found\nACTION: Check your 'census-bdys-path' argument")
    else:
        # load files in separate processes
        utils.multiprocess_shapefile_load(create_list, settings, logger)

        # Run the appends one at a time (Can't multi process as large sets of parallel INSERTs cause database deadlocks)
        # utils.multiprocess_shapefile_load(append_list, settings, logger)
        for shp in append_list:
            utils.import_shapefile_to_postgres(pg_cur, shp['file_path'], shp['pg_table'], shp['pg_schema'],
                                               shp['delete_table'], True)

        logger.info("\t- Step 1 of 3 : raw census boundaries loaded : {0}".format(datetime.now() - start_time))


def prep_boundaries(pg_cur, settings):
    # Step 2 of 3 : create admin bdy tables read to be used
    start_time = datetime.now()

    if settings['boundaries_schema'] != "public":
        pg_cur.execute("CREATE SCHEMA IF NOT EXISTS {0} AUTHORIZATION {1}"
                       .format(settings['boundaries_schema'], settings['pg_user']))

    # create tables using multiprocessing - using flag in file to split file up into sets of statements
    sql_list = utils.open_sql_file("02-02a-prep-admin-bdys-tables.sql", settings).split("-- # --")
    sql_list = sql_list + utils.open_sql_file("02-02b-prep-census-2011-bdys-tables.sql", settings).split("-- # --")
    sql_list = sql_list + utils.open_sql_file("02-02c-prep-census-2016-bdys-tables.sql", settings).split("-- # --")

    # # Account for bdys that are not in states to load - not yet working
    # for sql in sql_list:
    #     if settings['states_to_load'] == ['OT'] and '.commonwealth_electorates ' in sql:
    #         sql_list.remove(sql)
    #
    #     if settings['states_to_load'] == ['ACT'] and '.local_government_areas ' in sql:
    #         sql_list.remove(sql)
    #
    #     logger.info(settings['states_to_load']
    #
    #     if not ('NT' in settings['states_to_load'] or 'SA' in settings['states_to_load']
    #             or 'VIC' in settings['states_to_load'] or 'WA' in settings['states_to_load']) \
    #             and '.local_government_wards ' in sql:
    #         sql_list.remove(sql)
    #
    #     if settings['states_to_load'] == ['OT'] and '.state_lower_house_electorates ' in sql:
    #         sql_list.remove(sql)
    #
    #     if not ('TAS' in settings['states_to_load'] or 'VIC' in settings['states_to_load']
    #             or 'WA' in settings['states_to_load']) and '.state_upper_house_electorates ' in sql:
    #         sql_list.remove(sql)

    utils.multiprocess_list("sql", sql_list, settings, logger)

    # Special case - remove custom outback bdy if South Australia not requested
    if 'SA' not in settings['states_to_load']:
        pg_cur.execute(utils.prep_sql("DELETE FROM admin_bdys.locality_bdys WHERE locality_pid = 'SA999999'", settings))
        pg_cur.execute(utils.prep_sql("VACUUM ANALYZE admin_bdys.locality_bdys", settings))

    logger.info("\t- Step 2 of 3 : admin boundaries prepped : {0}".format(datetime.now() - start_time))


def create_boundaries_for_analysis(settings):
    # Step 3 of 3 : create admin bdy tables optimised for spatial analysis
    start_time = datetime.now()

    if settings['st_subdivide_supported']:
        template_sql = utils.open_sql_file("02-03-create-admin-bdy-analysis-tables_template.sql", settings)
        sql_list = list()

        for table in settings['admin_bdy_list']:
            sql = template_sql.format(table[0], table[1])
            if table[0] == 'locality_bdys':  # special case, need to change schema name
                # sql = sql.replace(settings['boundaries_schema'], settings['boundaries_schema'])
                sql = sql.replace("name", "locality_name")
            sql_list.append(sql)
        utils.multiprocess_list("sql", sql_list, settings, logger)
        logger.info("\t- Step 3 of 3 : admin boundaries for analysis created : {0}".format(datetime.now() - start_time))
    else:
        logger.warning("\t- Step 3 of 3 : admin boundaries for analysis NOT created - "
                       "requires PostGIS 2.2+ with GEOS 3.5.0+")


# # create gnaf reference tables by flattening raw gnaf address, streets & localities into a usable form
# # also creates all supporting lookup tables and usable admin bdy tables
# def create_reference_tables(pg_cur, settings):
#     # set postgres search path back to the default
#     pg_cur.execute("SET search_path = public, pg_catalog")
#
#     # create schemas
#     if settings['data_schema'] != "public":
#         pg_cur.execute("CREATE SCHEMA IF NOT EXISTS {0} AUTHORIZATION {1}"
#                        .format(settings['data_schema'], settings['pg_user']))
#
#     # Step 1 of 14 : create reference tables
#     start_time = datetime.now()
#     pg_cur.execute(utils.open_sql_file("03-01-reference-create-tables.sql", settings))
#     logger.info("\t- Step  1 of 14 : create reference tables : {0}".format(datetime.now() - start_time))
#
#     # Step 2 of 14 : populate localities
#     start_time = datetime.now()
#     pg_cur.execute(utils.open_sql_file("03-02-reference-populate-localities.sql", settings))
#     logger.info("\t- Step  2 of 14 : localities populated : {0}".format(datetime.now() - start_time))
#
#     # Step 3 of 14 : populate locality aliases
#     start_time = datetime.now()
#     pg_cur.execute(utils.open_sql_file("03-03-reference-populate-locality-aliases.sql", settings))
#     logger.info("\t- Step  3 of 14 : locality aliases populated : {0}".format(datetime.now() - start_time))
#
#     # Step 4 of 14 : populate locality neighbours
#     start_time = datetime.now()
#     pg_cur.execute(utils.open_sql_file("03-04-reference-populate-locality-neighbours.sql", settings))
#     logger.info("\t- Step  4 of 14 : locality neighbours populated : {0}".format(datetime.now() - start_time))
#
#     # Step 5 of 14 : populate streets
#     start_time = datetime.now()
#     pg_cur.execute(utils.open_sql_file("03-05-reference-populate-streets.sql", settings))
#     logger.info("\t- Step  5 of 14 : streets populated : {0}".format(datetime.now() - start_time))
#
#     # Step 6 of 14 : populate street aliases
#     start_time = datetime.now()
#     pg_cur.execute(utils.open_sql_file("03-06-reference-populate-street-aliases.sql", settings))
#     logger.info("\t- Step  6 of 14 : street aliases populated : {0}".format(datetime.now() - start_time))
#
#     # Step 7 of 14 : populate addresses, using multiprocessing
#     start_time = datetime.now()
#     sql = utils.open_sql_file("03-07-reference-populate-addresses-1.sql", settings)
#     sql_list = utils.split_sql_into_list(pg_cur, sql, settings['data_schema'],
# "streets", "str", "gid", settings, logger)
#     if sql_list is not None:
#         utils.multiprocess_list('sql', sql_list, settings, logger)
#     pg_cur.execute(utils.prep_sql("ANALYZE gnaf.temp_addresses;", settings))
#     logger.info("\t- Step  7 of 14 : addresses populated : {0}".format(datetime.now() - start_time))
#
#     # Step 8 of 14 : populate principal alias lookup
#     start_time = datetime.now()
#     pg_cur.execute(utils.open_sql_file("03-08-reference-populate-address-alias-lookup.sql", settings))
#     logger.info("\t- Step  8 of 14 : principal alias lookup populated : {0}".format(datetime.now() - start_time))
#
#     # Step 9 of 14 : populate primary secondary lookup
#     start_time = datetime.now()
#     pg_cur.execute(utils.open_sql_file("03-09-reference-populate-address-secondary-lookup.sql", settings))
#     pg_cur.execute(utils.prep_sql("VACUUM ANALYSE gnaf.address_secondary_lookup", settings))
#     logger.info("\t- Step  9 of 14 : primary secondary lookup populated : {0}".format(datetime.now() - start_time))
#
#     # Step 10 of 14 : split the Melbourne locality into its 2 postcodes (3000, 3004)
#     start_time = datetime.now()
#     pg_cur.execute(utils.open_sql_file("03-10-reference-split-melbourne.sql", settings))
#     logger.info("\t- Step 10 of 14 : Melbourne split : {0}".format(datetime.now() - start_time))
#
#     # Step 11 of 14 : finalise localities assigned to streets and addresses
#     start_time = datetime.now()
#     pg_cur.execute(utils.open_sql_file("03-11-reference-finalise-localities.sql", settings))
#     logger.info("\t- Step 11 of 14 : localities finalised : {0}".format(datetime.now() - start_time))
#
#     # Step 12 of 14 : finalise addresses, using multiprocessing
#     start_time = datetime.now()
#     sql = utils.open_sql_file("03-12-reference-populate-addresses-2.sql", settings)
#     sql_list = utils.split_sql_into_list(pg_cur, sql, settings['data_schema'], "localities", "loc", "gid",
#                                         settings, logger)
#     if sql_list is not None:
#         utils.multiprocess_list('sql', sql_list, settings, logger)
#
#     # turf the temp address table
#     pg_cur.execute(utils.prep_sql("DROP TABLE IF EXISTS gnaf.temp_addresses", settings))
#     logger.info("\t- Step 12 of 14 : addresses finalised : {0}".format(datetime.now() - start_time))
#
#     # Step 13 of 14 : create almost correct postcode boundaries by aggregating localities, using multiprocessing
#     start_time = datetime.now()
#     sql = utils.open_sql_file("03-13-reference-derived-postcode-bdys.sql", settings)
#     sql_list = []
#     for state in settings['states_to_load']:
#         state_sql = sql.replace("GROUP BY ", "WHERE state = '{0}' GROUP BY ".format(state))
#         sql_list.append(state_sql)
#     utils.multiprocess_list("sql", sql_list, settings, logger)
#
#     # create analysis table?
#     if settings['st_subdivide_supported']:
#         pg_cur.execute(utils.open_sql_file("03-13a-create-postcode-analysis-table.sql", settings))
#
#     logger.info("\t- Step 13 of 14 : postcode boundaries created : {0}".format(datetime.now() - start_time))
#
#     # Step 14 of 14 : create indexes, primary and foreign keys, using multiprocessing
#     start_time = datetime.now()
#     raw_sql_list = utils.open_sql_file("03-14-reference-create-indexes.sql", settings).split("\n")
#     sql_list = []
#     for sql in raw_sql_list:
#         if sql[0:2] != "--" and sql[0:2] != "":
#             sql_list.append(sql)
#     utils.multiprocess_list("sql", sql_list, settings, logger)
#     logger.info("\t- Step 14 of 14 : create primary & foreign keys and indexes : {0}"
#                 .format(datetime.now() - start_time))
#
#
# def boundary_tag_gnaf(pg_cur, settings):
#
#     # create bdy table list
#     # remove localities, postcodes and states as these IDs are already assigned to GNAF addresses
#     table_list = list()
#     for table in settings['admin_bdy_list']:
#         if table[0] not in ["locality_bdys", "postcode_bdys", "state_bdys"]:
#             # if no analysis tables created - use the full tables instead of the subdivided ones
#             # WARNING: this can add hours to the processing
#             if settings['st_subdivide_supported']:
#                 table_name = "{0}_analysis".format(table[0], )
#             else:
#                 table_name = table[0]
#
#             table_list.append([table_name, table[1]])
#
#     # create bdy tagged address table
#     pg_cur.execute("DROP TABLE IF EXISTS {0}.address_admin_boundaries CASCADE".format(settings['data_schema'], ))
#     create_table_list = list()
#     create_table_list.append("CREATE TABLE {0}.address_admin_boundaries (gid serial NOT NULL,"
#                              "gnaf_pid text NOT NULL,"
#                              "alias_principal character(1) NOT NULL,"
#                              "locality_pid text NOT NULL,"
#                              "locality_name text NOT NULL,"
#                              "postcode text,"
#                              "state text NOT NULL"
#                              .format(settings['data_schema'], ))
#     for table in table_list:
#         pid_field = table[1]
#         name_field = pid_field.replace("_pid", "_name")
#         create_table_list.append(", {0} text, {1} text"
#                                  .format(pid_field, name_field))
#     create_table_list.append(") WITH (OIDS=FALSE);ALTER TABLE {0}.address_admin_boundaries OWNER TO {1}"
#                              .format(settings['data_schema'], settings['pg_user']))
#     pg_cur.execute("".join(create_table_list))
#
#     i = 0
#
#     for address_table in ["address_principals", "address_aliases"]:
#
#         # Step 1/4 of 8 : tag gnaf addresses with admin boundary IDs, using multiprocessing
#         start_time = datetime.now()
#
#         # create temp tables
#         template_sql = utils.open_sql_file("04-01a-bdy-tag-create-table-template.sql", settings)
#         for table in table_list:
#             pg_cur.execute(template_sql.format(table[0],))
#
#         # create temp tables of bdy tagged gnaf_pids
#         template_sql = utils.open_sql_file("04-01b-bdy-tag-template.sql", settings)
#         sql_list = list()
#         for table in table_list:
#             sql = template_sql.format(table[0], table[1])
#
#             short_sql_list = utils.split_sql_into_list(pg_cur, sql, settings['boundaries_schema'], table[0],
#                                                       "bdys", "gid", settings, logger)
#
#             if short_sql_list is not None:
#                 sql_list.extend(short_sql_list)
#
#         # logger.info('\n'.join(sql_list))
#
#         if sql_list is not None:
#             utils.multiprocess_list("sql", sql_list, settings, logger)
#
#         i += 1
#         logger.info("\t- Step {0} of 8 : {1} - gnaf addresses tagged with admin boundary IDs: {2}"
#                     .format(i, address_table, datetime.now() - start_time))
#         start_time = datetime.now()
#
#         # Step 2/5 of 8 : delete invalid matches, create indexes and analyse tables
#         sql_list = list()
#         for table in table_list:
#             sql = "DELETE FROM {0}.temp_{1}_tags WHERE gnaf_state <> bdy_state AND gnaf_state <> 'OT';" \
#                   "CREATE INDEX temp_{1}_tags_gnaf_pid_idx ON {0}.temp_{1}_tags USING btree(gnaf_pid);" \
#                   "ANALYZE {0}.temp_{1}_tags".format(settings['data_schema'], table[0])
#             sql_list.append(sql)
#         utils.multiprocess_list("sql", sql_list, settings, logger)
#
#         i += 1
#         logger.info("\t- Step {0} of 8 : {1} - invalid matches deleted & bdy tag indexes created : {2}"
#                     .format(i, address_table, datetime.now() - start_time))
#         start_time = datetime.now()
#
#         # Step 3/6 of 8 : insert boundary tagged addresses
#
#         # create insert statement for multiprocessing
#         insert_field_list = list()
#         insert_field_list.append("(gnaf_pid, alias_principal, locality_pid, locality_name, postcode, state")
#
#         insert_join_list = list()
#         insert_join_list.append("FROM {0}.{1} AS pnts ".format(settings['data_schema'], address_table))
#
#         select_field_list = list()
#         select_field_list.append("SELECT pnts.gnaf_pid, pnts.alias_principal, pnts.locality_pid, "
#                                  "pnts.locality_name, pnts.postcode, pnts.state")
#
#         drop_table_list = list()
#
#         for table in table_list:
#             pid_field = table[1]
#             name_field = pid_field. replace("_pid", "_name")
#             insert_field_list.append(", {0}, {1}".format(pid_field, name_field))
#             select_field_list.append(", temp_{0}_tags.bdy_pid, temp_{0}_tags.bdy_name ".format(table[0]))
#             insert_join_list.append("LEFT OUTER JOIN {0}.temp_{1}_tags ON pnts.gnaf_pid = temp_{1}_tags.gnaf_pid "
#                                     .format(settings['data_schema'], table[0]))
#             drop_table_list.append("DROP TABLE IF EXISTS {0}.temp_{1}_tags;"
# .format(settings['data_schema'], table[0]))
#
#         insert_field_list.append(") ")
#
#         insert_statement_list = list()
#         insert_statement_list.append("INSERT INTO {0}.address_admin_boundaries ".format(settings['data_schema'],))
#         insert_statement_list.append("".join(insert_field_list))
#         insert_statement_list.append("".join(select_field_list))
#         insert_statement_list.append("".join(insert_join_list))
#
#         sql = "".join(insert_statement_list) + ";"
#         sql_list = utils.split_sql_into_list(pg_cur, sql, settings['data_schema'], address_table, "pnts", "gid",
#                                             settings, logger)
#         # logger.info("\n".join(sql_list)
#
#         if sql_list is not None:
#             utils.multiprocess_list("sql", sql_list, settings, logger)
#
#         # drop temp tables
#         pg_cur.execute("".join(drop_table_list))
#
#         # get stats
#         pg_cur.execute("ANALYZE {0}.address_admin_boundaries ".format(settings['data_schema']))
#
#         i += 1
#         logger.info("\t- Step {0} of 8 : {1} - bdy tags added to output table : {2}"
#                     .format(i, address_table, datetime.now() - start_time))
#
#     start_time = datetime.now()
#
#     # Step 7 of 8 : add index to output table
#     sql = "CREATE INDEX address_admin_boundaries_gnaf_pid_idx ON {0}.address_admin_boundaries USING btree (gnaf_pid)"\
#         .format(settings['data_schema'])
#     pg_cur.execute(sql)
#
#     i += 1
#     logger.info("\t- Step {0} of 8 : created index on bdy tagged address table : {1}"
#                 .format(i, datetime.now() - start_time))
#     start_time = datetime.now()
#
#     # Step 8 of 8 : log duplicates - happens when 2 boundaries overlap by a very small amount
#     # (can be ignored if there's a small number of records affected)
#     sql = "SELECT gnaf_pid FROM (SELECT Count(*) AS cnt, gnaf_pid FROM {0}.address_admin_boundaries " \
#           "GROUP BY gnaf_pid) AS sqt WHERE cnt > 1".format(settings['data_schema'])
#     pg_cur.execute(sql)
#
#     i += 1
#
#     try:
#         duplicates = pg_cur.fetchall()
#         gnaf_pids = list()
#
#         for duplicate in duplicates:
#             gnaf_pids.append("\t\t" + duplicate[0])
#
#         logger.warning("\t- Step {0} of 8 : found boundary tag duplicates : {1}"
# .format(i, datetime.now() - start_time))
#         logger.warning("\n".join(gnaf_pids))
#     except psycopg2.Error:
#         logger.info("\t- Step {0} of 8 : no boundary tag duplicates : {1}".format(i, datetime.now() - start_time))


# get row counts of tables in each schema, by state, for visual QA
def create_qa_tables(pg_cur, settings):
    start_time = datetime.now()

    i = 0

    for schema in [settings['data_schema'], settings['boundaries_schema']]:

        i += 1

        # create qa table of rows counts
        sql = "DROP TABLE IF EXISTS {0}.qa;" \
              "CREATE TABLE {0}.qa (table_name text, aus integer, act integer, nsw integer, " \
              "nt integer, ot integer, qld integer, sa integer, tas integer, vic integer, wa integer) " \
              "WITH (OIDS=FALSE);" \
              "ALTER TABLE {0}.qa OWNER TO {1}".format(schema, settings['pg_user'])
        pg_cur.execute(sql)

        # get table names in schema
        sql = "SELECT table_name FROM information_schema.tables WHERE table_schema = '{0}' AND table_name <> 'qa' " \
              "ORDER BY table_name"\
            .format(schema)
        pg_cur.execute(sql)

        table_names = []
        for pg_row in pg_cur:
            table_names.append(pg_row[0])

        # get row counts by state
        for table_name in table_names:
            sql = "INSERT INTO {0}.qa " \
                  "SELECT '{1}', SUM(AUS), SUM(ACT), SUM(NSW), SUM(NT), SUM(OT), " \
                  "SUM(QLD), SUM(SA), SUM(TAS), SUM(VIC), SUM(WA) " \
                  "FROM (" \
                  "SELECT 1 AS AUS," \
                  "CASE WHEN state = 'ACT' THEN 1 ELSE 0 END AS ACT," \
                  "CASE WHEN state = 'NSW' THEN 1 ELSE 0 END AS NSW," \
                  "CASE WHEN state = 'NT' THEN 1 ELSE 0 END AS NT," \
                  "CASE WHEN state = 'OT' THEN 1 ELSE 0 END AS OT," \
                  "CASE WHEN state = 'QLD' THEN 1 ELSE 0 END AS QLD," \
                  "CASE WHEN state = 'SA' THEN 1 ELSE 0 END AS SA," \
                  "CASE WHEN state = 'TAS' THEN 1 ELSE 0 END AS TAS," \
                  "CASE WHEN state = 'VIC' THEN 1 ELSE 0 END AS VIC," \
                  "CASE WHEN state = 'WA' THEN 1 ELSE 0 END AS WA " \
                  "FROM {0}.{1}) AS sqt".format(schema, table_name)

            try:
                pg_cur.execute(sql)
            except psycopg2.Error:  # triggers when there is no state field in the table
                # change the query for an Australia count only
                sql = "INSERT INTO {0}.qa (table_name, aus) " \
                      "SELECT '{1}', Count(*) FROM {0}.{1}".format(schema, table_name)

                try:
                    pg_cur.execute(sql)
                except Exception as ex:
                    # if no state field - change the query for an Australia count only
                    logger.warning("Couldn't get row count for {0}.{1} : {2}".format(schema, table_name, ex))

        pg_cur.execute("ANALYZE {0}.qa".format(schema))

        logger.info("\t- Step {0} of 2 : got row counts for {1} schema : {2}"
                    .format(i, schema, datetime.now() - start_time))

    logger.info("")


if __name__ == '__main__':
    logger = logging.getLogger()

    # set logger
    log_file = os.path.abspath(__file__).replace(".py", ".log")
    logging.basicConfig(filename=log_file, level=logging.DEBUG, format="%(asctime)s %(message)s",
                        datefmt="%m/%d/%Y %I:%M:%S %p")

    # setup logger to write to screen as well as writing to log file
    # define a Handler which writes INFO messages or higher to the sys.stderr
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    # set a format which is simpler for console use
    formatter = logging.Formatter('%(name)-12s: %(levelname)-8s %(message)s')
    # tell the handler to use this format
    console.setFormatter(formatter)
    # add the handler to the root logger
    logging.getLogger('').addHandler(console)

    logger.info("")
    logger.info("Start census-loader")
    utils.check_python_version(logger)

    if main():
        logger.info("Finished successfully!")
    else:
        logger.fatal("Something bad happened!")

    logger.info("")
    logger.info("-------------------------------------------------------------------------------")
