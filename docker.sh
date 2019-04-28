# Start two containers, one with etcd, one with placement.
docker run -d --rm --name etcd -v /data/etcd/:/data \
          -p 2379:2379 -p 2380:2380 xieyanze/etcd3:latest
# Placement is configued by the variables in dockerenv, including
# pointing to an external database WHICH YOU MUST SET UP.
docker run -dt --rm --name placement -p 8080:80 --env-file dockerenv \
       cdent/placedock:latest
