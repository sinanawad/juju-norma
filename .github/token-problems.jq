# Why a `charmcraft whoami --format json` token cannot publish $charm, one
# reason per line; no output means it can. Used by token-health.yaml.
if .logged == false then "not logged in: is the secret set?" else
# whoami lists no permissions for an unrestricted token, like charms/channels.
(if .permissions then
   .permissions as $p
   | ["package-manage-revisions", "package-manage-releases"][] as $need
   | select(($p | index("package-manage")) == null and ($p | index($need)) == null)
   | "missing permission \($need)"
 else empty end),
  (if .charms and ((.charms | map(.name) | index($charm)) == null)
   then "not valid for charm \($charm)" else empty end),
  (if .channels
   then (["latest/edge", "latest/candidate", "latest/stable"][] as $c
         | select((.channels | index($c)) == null) | "not valid for channel \($c)")
   else empty end)
end
