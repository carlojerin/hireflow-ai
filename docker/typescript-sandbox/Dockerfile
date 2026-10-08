# JobPilot AI - TypeScript sandbox image
#
# node:18-slim doesn't bundle the TypeScript compiler -- this adds
# it once, at build time (with network access), so execution time
# (docker_sandbox_runner.py, --network none) never needs the
# internet at all.
#
# @types/node is also installed globally -- without it, tsc doesn't
# know about Node's own globals (require, process, fs, ...), so any
# candidate solution that reads stdin the normal Node way fails to
# even COMPILE with "Cannot find name 'require'". NODE_PATH/
# TS_NODE_COMPILER_OPTIONS alone don't make tsc look in npm's global
# node_modules for @types -- --typeRoots has to point there
# explicitly, which docker_sandbox_runner.py's compile_cmd does.
FROM node:18-slim
RUN npm install -g typescript@5.4.5 @types/node@20
