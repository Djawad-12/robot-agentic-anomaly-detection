# Helper image for the network faults: it joins URSim's network namespace
# (`docker run --net container:ursim --cap-add NET_ADMIN`) and changes it with tc and iptables.
FROM alpine:3.20
RUN apk add --no-cache iproute2 iptables
