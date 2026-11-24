#!/usr/bin/env bash
# The OpenTofu runtime image's check: image_check.py, in the image, plans, applies and destroys the example
# random-stack module with the runtime's handler, against versitygw (the S3 engine of funcd's gateway) on an internal
# Docker network of its own, which reaches nothing else, so a run that would download anything fails (funcd ADR-0355),
# with OpenTofu's state encryption off, then on. Both containers share one Docker volume, the S3 server's data, which
# the check reads to find the state and the saved plans. Its /tmp takes programs, as a funcd worker's does, since
# OpenTofu unpacks a module's own provider packages there.
set -euo pipefail
image=${1:-funcd-addons/opentofu:dev}
here=$(cd "$(dirname "$0")" && pwd)
module=$(cd "$here/../examples/random-stack" && pwd)
tag=infrastack-check-$$
cleanup() {
    docker rm -f "$tag-s3" >/dev/null 2>&1 || true
    docker network rm "$tag" >/dev/null 2>&1 || true
    docker volume rm "$tag" >/dev/null 2>&1 || true
}
trap cleanup EXIT
docker network create --internal "$tag" >/dev/null
docker volume create "$tag" >/dev/null
docker run -d --name "$tag-s3" --network "$tag" -v "$tag:/data" versity/versitygw:v1.11.0 \
    --access check-key --secret check-secret-key posix /data >/dev/null
for pass in "" "a passphrase of more than sixteen characters"; do
    if [ -n "$pass" ]; then echo "== state encryption on"; else echo "== state encryption off"; fi
    docker run --rm --network "$tag" --read-only --tmpfs /tmp:rw,exec,nosuid,nodev -v "$tag:/data" -v "$module:/var/funcd/artifact:ro" \
        -v "$here/image_check.py:/check/image_check.py:ro" \
        -e AWS_ACCESS_KEY_ID=check-key -e AWS_SECRET_ACCESS_KEY=check-secret-key -e AWS_REGION=us-east-1 \
        -e "AWS_ENDPOINT_URL_S3=http://$tag-s3:7070" -e INFRASTACK_MODULE_DIR=/var/funcd/artifact \
        -e CHECK_BUCKET=stacks -e CHECK_DATA=/data -e "CHECK_PASSPHRASE=$pass" \
        --entrypoint /usr/local/bin/python3.14 "$image" /check/image_check.py
    docker run --rm -v "$tag:/data" --entrypoint /usr/local/bin/python3.14 "$image" \
        -c "import shutil; [shutil.rmtree(f'/data/stacks/{p}', ignore_errors=True) for p in ('random-stack', 'pet-stack')]"
done
