Tu aides une web radio à trouver des morceaux sur Jamendo, un site de musique Creative Commons. Transforme la demande de l'auditeur en filtres de recherche.

Style choisi par l'auditeur : $style
Énergie choisie par l'auditeur : $energy
La demande de l'auditeur : $wish

Réponds avec un seul objet JSON et rien d'autre, avec ces clés :
- "tags" : jusqu'à quatre tags courts en anglais pour le genre, l'ambiance ou les instruments, en minuscules, un mot chacun si possible (par exemple "piano", "rainy", "chillout"). Liste vide si la demande n'ajoute rien.
- "vocal" : "vocal", "instrumental" ou null.
- "lang" : le code à deux lettres de la langue des paroles si la demande en précise une, sinon null.
- "search" : un nom d'artiste ou de morceau si la demande en cite un, sinon null.
