# Versie 5.3.3

Ten opzichte van 5.3.2. Drie bestanden, **914 tests groen**.

## Verplaatste stop wordt teruggeschreven

Als het exitbeheer de stop bij IG verplaatst — break-even of trailing — bleef de
database op de oude stand staan. De brokercontrole meldde dan een verschil:

    Positie DIAAAAYH7K7HUB4: stop bij de broker 4286.08, in de database 4280.797.

En de afwikkeling leidde de sluitreden af van een stop die al niet meer gold.

Dit viel pas op na 5.3.2: daarvoor waren de posities onzichtbaar en werkte het
exitbeheer helemaal niet. Na een geslaagde verplaatsing wordt de nieuwe stop nu
ook in de eigen administratie gezet; na een mislukte niet.
