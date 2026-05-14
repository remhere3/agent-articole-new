#!/bin/bash

if [[ $EUID -ne 0 ]]; then
   echo "Te rog rulează cu sudo."
   exit 1
fi

echo "==============================================================="
echo " SERVICII ACTIVE ȘI PORTURI ASCULTATE (lsof method)"
echo "==============================================================="
printf "%-25s %-10s %-20s\n" "SERVICIU" "PORT" "ADRESĂ"
echo "---------------------------------------------------------------"

# lsof -i -P -n : listează fișierele de rețea, porturile numerice, fără rezoluție DNS
# grep LISTEN : doar cele care ascultă
sudo lsof -i -P -n | grep LISTEN | awk '{
    # Numele procesului este în coloana 1
    # Adresa și portul sunt în coloana 9 (ex: 127.0.0.1:11434 sau *:80)
    split($9, a, ":");
    port = a[length(a)];
    
    printf "%-25s %-10s %-20s\n", $1, port, $9
}' | sort -n -k 2 -u

echo "==============================================================="