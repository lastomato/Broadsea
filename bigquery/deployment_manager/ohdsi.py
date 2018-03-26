# Copyright 2017 Google Inc. All rights reserved.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#     http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

COMPUTE_URL_BASE = 'https://www.googleapis.com/compute/v1/'

def global_compute_url(project, collection, name):
  return "%sprojects/%s/global/%s/%s" % (COMPUTE_URL_BASE, project, collection, name)

def zonal_compute_url(project, zone, collection, name):
  return "%sprojects/%s/zones/%s/%s/%s" % (COMPUTE_URL_BASE, project, zone, collection, name)

def cloud_config(context):
  values = {
    'base_name': context.env['deployment'],
    'container_project': context.properties['containerProject'],
    'project': context.env['project'],
    'cdm_dataset': context.properties['cdmDataset'],
    'ohdsi_dataset': context.properties['ohdsiDataset']
  }

  return """
#cloud-config
write_files:
- path: /etc/systemd/system/broadsea-methods.service
  permissions: 0644
  owner: root
  content: |
    [Unit]
    Description=Start the broadsea methods docker container
    Wants=gcr-online.target
    After=gcr-online.target

    [Service]
    Environment="HOME=/home/cloudservice"
    ExecStartPre=/usr/bin/docker-credential-gcr configure-docker
    ExecStart=/usr/bin/docker run --rm -u 0 -p 8787:8787 -p 6111:6111 --name=broadsea-methodslibrary -v /etc/ohdsi-scripts/:/ohdsi-scripts gcr.io/{container_project}/broadsea-methods-bigquery
    ExecStop=/usr/bin/docker stop broadsea-methodslibrary
    ExecStopPost=/usr/bin/docker rm broadsea-methodslibrary
    Restart=on-failure

- path: /etc/systemd/system/broadsea-webtools.service
  permissions: 0644
  owner: root
  content: |
    [Unit]
    Description=Start the broadsea webtools docker container
    Wants=gcr-online.target
    After=gcr-online.target

    [Service]
    Environment="HOME=/home/cloudservice"
    ExecStartPre=/usr/bin/docker-credential-gcr configure-docker
    ExecStart=/usr/bin/docker run --rm -u 0 -p 8080:8080 --name=broadsea-webtools \
      -v /etc/ohdsi-scripts/:/ohdsi-scripts \
      --env=WEBAPI_URL=http://localhost:8080 \
      --env=ENV=webapi-postgresql \
      --env=DATASOURCE_DRIVERCLASSNAME=org.postgresql.Driver \
      --env=DATASOURCE_URL=jdbc:postgresql://$(ref.{base_name}-postgres-instance.ipAddresses[0].ipAddress):5432/postgres \
      --env=DATASOURCE_CDM_SCHEMA=cdm \
      --env=DATASOURCE_OHDSI_SCHEMA=ohdsi \
      --env=DATASOURCE_USERNAME=ohdsi-postgres-user \
      --env=DATASOURCE_PASSWORD=ohdsi-postgres-password \
      --env=SPRING_JPA_PROPERTIES_HIBERNATE_DEFAULT_SCHEMA=ohdsi \
      --env=SPRING_JPA_PROPERTIES_HIBERNATE_DIALECT=org.hibernate.dialect.PostgreSQLDialect \
      --env=SPRING_BATCH_REPOSITORY_TABLEPREFIX=ohdsi.BATCH_ \
      --env=FLYWAY_DATASOURCE_DRIVERCLASSNAME=org.postgresql.Driver \
      --env=FLYWAY_DATASOURCE_URL=jdbc:postgresql://$(ref.{base_name}-postgres-instance.ipAddresses[0].ipAddress):5432/postgres \
      --env=FLYWAY_SCHEMAS=ohdsi \
      --env=FLYWAY_PLACEHOLDERS_OHDSISCHEMA=ohdsi \
      --env=FLYWAY_DATASOURCE_USERNAME=ohdsi-postgres-user \
      --env=FLYWAY_DATASOURCE_PASSWORD=ohdsi-postgres-password \
      --env=FLYWAY_LOCATIONS=classpath:db/migration/postgresql \
      gcr.io/{container_project}/broadsea-webtools-bigquery
    ExecStop=/usr/bin/docker stop broadsea-webtools
    ExecStopPost=/usr/bin/docker rm broadsea-webtools
    Restart=on-failure

- path: /etc/ohdsi-scripts/source_source_daimon.sql
  permissions: 0666
  owner: root
  content: |
    -- remove any previously added database connection configuration data
    truncate ohdsi.source;
    truncate ohdsi.source_daimon;
    
    -- OHDSI CDM source
    INSERT INTO ohdsi.source(source_id, source_name, source_key, source_connection, source_dialect)
    VALUES (1, 'OHDSI CDM V5 Database', 'OHDSI-CDMV5', 'jdbc:BQDriver:{project}', 'bigquery');
    
    -- CDM daimon
    INSERT INTO ohdsi.source_daimon( source_daimon_id, source_id, daimon_type, table_qualifier, priority) VALUES (1, 1, 0, '{cdm_dataset}', 2);
    
    -- VOCABULARY daimon
    INSERT INTO ohdsi.source_daimon( source_daimon_id, source_id, daimon_type, table_qualifier, priority) VALUES (2, 1, 1, '{cdm_dataset}', 2);
    
    -- RESULTS daimon
    INSERT INTO ohdsi.source_daimon( source_daimon_id, source_id, daimon_type, table_qualifier, priority) VALUES (3, 1, 2, '{ohdsi_dataset}', 2);
    
    -- EVIDENCE daimon
    INSERT INTO ohdsi.source_daimon( source_daimon_id, source_id, daimon_type, table_qualifier, priority) VALUES (4, 1, 3, '{ohdsi_dataset}', 2);

- path: /etc/ohdsi-scripts/runAchilles.R
  permissions: 0666
  owner: root
  content: |
    install.packages("devtools")
    library(devtools)
    install.packages("rJava",type='source')
    #install_local(path="/ohdsi-deployment/rJava")
    install_local(path="/ohdsi-deployment/SqlRender")
    install_local(path="/ohdsi-deployment/DatabaseConnector")
    install_local(path="/ohdsi-deployment/Achilles")
    library(Achilles)
    connectionDetails = createConnectionDetails(dbms="bigquery", server="{project}", pathToDriver = "/tmp/drivers", user="", password="")
    achilles(connectionDetails, cdmDatabaseSchema="{cdm_dataset}", resultsDatabaseSchema="{ohdsi_dataset}", sourceName="OHDSI CDM V5 Database", cdmVersion = "5", vocabDatabaseSchema = "{cdm_dataset}", runHeel=TRUE, conceptHierarchy = TRUE, createIndices = FALSE)

- path: /etc/ohdsi-scripts/update_source.py
  permissions: 0555
  owner: root
  content: |
    #!/usr/bin/python
    import subprocess
    import time
    
    def getContainerId():
      return subprocess.Popen(['docker', 'ps', '-aqf' 'name=broadsea-webtools'], stdout=subprocess.PIPE).communicate()[0].strip()
    
    def runQuery(query):
      CMD=['docker', 'exec', getContainerId(), '/usr/bin/psql',
           'host=$(ref.{base_name}-postgres-instance.ipAddresses[0].ipAddress) dbname=postgres user=ohdsi-postgres-user password=ohdsi-postgres-password',
           '-A', '-t'] + query
      return subprocess.Popen(CMD, stdout=subprocess.PIPE).communicate()[0]
    
    def runCommand(cmd):
      return subprocess.Popen(cmd, stdout=subprocess.PIPE).communicate()[0]
    
    # Wait for table to be created
    while True:
      table_count = runQuery(['-c', 'SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=\'ohdsi\' AND table_name=\'source\';'])
      if len(table_count.strip()) > 0 and int(table_count) > 0: break
      print "Did not find ohdsi.source table. Sleeping for 10 seconds"
      time.sleep(10)
    print "Found ohdsi.source table"
    
    # Populate the table if it's empty
    while True:
      source_count = runQuery(['-c', 'SELECT COUNT(*) FROM ohdsi.source'])
      print "Found %d rows in ohdsi.source" % int(source_count)
      if int(source_count) == 0:
        container_id  = getContainerId()
        print runQuery(['-f', '/ohdsi-scripts/source_source_daimon.sql'])
        runCommand(['docker', 'restart', container_id])
      print "Sleeping for 10 minutes"
      time.sleep(600)

- path: /etc/systemd/system/broadsea-update-source.service
  permissions: 0644
  owner: root
  content: |
    [Unit]
    Description=Update the source in the postgres database

    [Service]
    ExecStart=/etc/ohdsi-scripts/update_source.py
    Restart=on-failure

- path: /etc/ohdsi-scripts/tail_weblog.sh
  permissions: 0555
  owner: root
  content: |
    #!/bin/bash
    # Utility to view the tail of the webtools log
    export WEBTOOLS_ID=`docker ps -aqf "name=broadsea-webtools"`
    export LOGS=`docker exec $WEBTOOLS_ID /usr/bin/find /var/log/supervisor | grep std`
    docker exec -i -t $WEBTOOLS_ID tail -f $LOGS

runcmd:
- systemctl daemon-reload
- systemctl start broadsea-methods.service
- systemctl start broadsea-webtools.service
- systemctl start broadsea-update-source.service
  """.format(**values)

def generate_config(context):
  resources = [
    {
      'name': "%s-address" % context.env['deployment'],
      'type': 'compute.beta.address',
      'properties': {
        'status': 'RESERVED',
        'addressType': 'EXTERNAL',
        'region': context.properties['region']
      }
    },
    {
      'name': "%s-vm" % context.env['deployment'],
      'type': 'compute.v1.instance',
      'properties': {
        'zone': context.properties['zone'],
        'machineType': zonal_compute_url(context.env['project'], context.properties['zone'], 'machineTypes', 'n1-standard-1'),
        'serviceAccounts': [{
          'email': "%s-compute@developer.gserviceaccount.com" % context.env['project_number'],
          'scopes': ['https://www.googleapis.com/auth/cloud-platform']
        }],
        'metadata': {
          'items': [{
            'key': 'user-data',
            'value': cloud_config(context)
          }]
        },
        'disks': [{
          'deviceName': 'boot',
          'type': 'PERSISTENT',
          'autoDelete': True,
          'boot': True,
          'initializeParams': {
            'diskName': "%s-disk" % context.env['deployment'],
            'sourceImage': global_compute_url('cos-cloud', 'images', 'cos-stable-61-9765-79-0')
          }
        }],
        'networkInterfaces': [{
          'accessConfigs': [{
            'name': 'external-nat',
            'type': 'ONE_TO_ONE_NAT',
            'natIP': "$(ref.%s-address.address)" % context.env['deployment']
          }],
          'network': global_compute_url(context.env['project'], 'networks', 'default')
        }]
      }
    },
    {
      'name': "%s-postgres-instance" % context.env['deployment'],
      'type': 'sqladmin.v1beta4.instance',
      'properties': {
        'name': "%s-postgres-%s" % (context.env['deployment'], context.properties['postgresInstanceSuffix']),
        'databaseVersion': 'POSTGRES_9_6',
        'gceZone': context.properties['zone'],
        'settings': {
          'tier': 'db-custom-1-3840',
          'ipConfiguration': {
            'authorizedNetworks': [{
              'name': 'broadsea-vm',
              'value': "$(ref.%s-address.address)" % context.env['deployment']
            }]
          }
        }
      }
    },
    {
      'name': 'tempDatasetResource',
      'type': 'bigquery.v2.dataset',
      'properties': {
        'datasetReference': {
          'datasetId' : context.properties['tempDataset']
        },
        'defaultTableExpirationMs': 86400000
      }
    },
    {
      'name': 'cdmDatasetResource',
      'type': 'bigquery.v2.dataset',
      'properties': {
        'datasetReference': {
          'datasetId' : context.properties['cdmDataset']
        }
      }
    },
    {
      'name': 'ohdsiDatasetResource',
      'type': 'bigquery.v2.dataset',
      'properties': {
        'datasetReference': {
          'datasetId' : context.properties['ohdsiDataset']
        }
      }
    }
  ]

  return {'resources': resources}