# Versatile Thermostat SmartPI

[Read in English](README.md) · [Lire la version française](README.fr.md)

<p align="center">
  <img src="https://github.com/KipK/vtherm_smartpi/raw/master/assets/brand/logo.png" alt="SmartPI Logo" width="300" />
</p>

<p align="center">
  <strong>Regulacja PI termostatów, która sama się dostosowuje — dla Home Assistant</strong>
</p>

<p align="center">
  Regulator PI oparty na modelu, który uczy się, jak twój pokój się nagrzewa i stygnie, i sam dopasowuje regulację — lepiej niż metody ze stałymi współczynnikami.
</p>

> [!IMPORTANT]
> Tak jak TPI, SmartPI jest dostępny tylko dla VTherm ustawionych na przełączniku (over switch), na zaworze (over valve) albo na encji climate ze sterowaniem zaworem.

## 🌟 Czym jest SmartPI?

SmartPI to zaawansowany algorytm regulacji temperatury typu PI. Opiera się na modelu cieplnym pierwszego rzędu (1R1C: jeden opór cieplny i jedna pojemność cieplna), a nie na klasycznej pętli PID. Uczy się, jak mocno ogrzewanie grzeje twój pokój, jak szybko pokój traci ciepło i jaki jest czas martwy (opóźnienie reakcji). Potem na podstawie tego modelu wylicza wysterowanie grzania dużo dokładniejsze niż regulatory czasowo-proporcjonalne o stałych ustawieniach.

- **Model cieplny 1R1C**: uczy się z rzeczywistych obserwacji wzmocnienia grzania, tempa strat ciepła i czasów martwych reakcji pokoju — bez ręcznego strojenia
- **Samodzielnie strojone wzmocnienia PI**: wylicza Kp i Ki automatycznie z nauczonej stałej czasowej i czasu martwego, według reguł IMC (strojenia na podstawie modelu) i reguł praktycznych (heurystycznych)
- **Część wyprzedzająca oparta na modelu (feed-forward)**: szacuje moc potrzebną do stałego utrzymania nastawy (temperatury zadanej); łączy to z powolną korektą przesunięcia i z korektą przewidującą FF3, domyślnie włączoną, która pomaga wrócić do normy po zakłóceniu
- **Trajektoria nastawy wyliczana ze wzoru**: kształtuje wartość odniesienia dla części proporcjonalnej, a przy grzaniu stosuje oparty na modelu limit „lądowania”, żeby szybko dojść do celu i ograniczyć przeregulowanie (przekroczenie celu)
- **Nadzór z bezpieczeństwem na pierwszym miejscu**: tabela decyzji oparta na reżimach pracy (rodzajach bieżącej sytuacji) zamraża albo odblokowuje uczenie i dopasowanie wzmocnień, zależnie od tego, co się teraz dzieje
- **Automatyczna kalibracja**: z czasem pilnuje jakości modelu i uruchamia ponowną kalibrację, gdy uczenie stoi w miejscu
- **Linearyzacja krzywej zaworu**: przelicza zapotrzebowanie SmartPI na położenie zaworu dopasowane do nierównego (nieliniowego) działania głowic grzejnikowych (TRV)
- **Bogata diagnostyka**: publikuje szczegółowy stan uczenia, stan modelu i dane regulacji — do podglądu w osobnej karcie Markdown Home Assistant albo w panelu Regulation karty [Equinox](https://github.com/KipK/equinox)

## 🔗 Współpraca z Versatile Thermostat

Ta integracja rozszerza popularną integrację [Versatile Thermostat](https://github.com/jmcollin78/versatile_thermostat): dodaje algorytm SmartPI jako wtyczkę. Versatile Thermostat już daje w Home Assistant pełne zarządzanie termostatami, a ze SmartPI dostajesz:

- algorytmy regulacji temperatury na profesjonalnym poziomie
- płynną współpracę z istniejącymi ustawieniami VT
- ustawienia osobne dla każdego urządzenia
- wspólne wartości domyślne dla łatwej konfiguracji, z własnymi ustawieniami każdego termostatu (rodzaj ogrzewania, czasy, krzywa zaworu) tam, gdzie ich potrzebuje

## 📊 Podgląd regulacji w Equinox

SmartPI publikuje uporządkowane podsumowanie diagnostyki, które [karta Lovelace Equinox](https://github.com/KipK/equinox) potrafi sama wykryć. Gdy Equinox jest zainstalowany i ustawiony dla twojej encji Versatile Thermostat, jego panel **Regulation** (regulacja) daje bezpośredni dostęp do uczenia SmartPI, wiarygodności modelu, rozbicia wysterowania na składniki, wykresów historii i działań serwisowych.

![Equinox SmartPI Regulation dashboard overview](https://github.com/KipK/equinox/raw/master/assets/screens/regul-overview.png)

![Equinox SmartPI Regulation learning section](https://github.com/KipK/equinox/raw/master/assets/screens/regul-learning.png)

## 📦 Instalacja

### Przez HACS (zalecane)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=KipK&repository=vtherm_smartpi&category=Integration)

1. Upewnij się, że w Home Assistant masz zainstalowany [HACS](https://hacs.xyz/)
2. Kliknij przycisk powyżej albo ręcznie dodaj to repozytorium w HACS
3. Wyszukaj w HACS „Versatile Thermostat SmartPI”
4. Zainstaluj integrację
5. Uruchom ponownie Home Assistant
6. Dodaj integrację `Vtherm_smartpi` w Ustawienia / Urządzenia oraz usługi / Integracje (Settings / Devices & Services / Integrations)
7. Ustaw algorytm SmartPI w swoich urządzeniach Versatile Thermostat

### Instalacja ręczna

1. Pobierz najnowsze wydanie ze strony [Releases](https://github.com/KipK/vtherm_smartpi/releases) (wydania)
2. Rozpakuj plik `vtherm_smartpi.zip`
3. Skopiuj folder `custom_components/vtherm_smartpi` do katalogu `custom_components` w Home Assistant
4. Uruchom ponownie Home Assistant
5. Skonfiguruj jak wyżej

## 📖 Dokumentacja

Pełna dokumentacja jest dostępna po polsku:

- 🇵🇱 [Dokumentacja użytkownika (po polsku)](documentation/pl/vtherm_smartpi.md)
- 🇵🇱 [Dokumentacja techniczna (po polsku)](documentation/pl/technical_doc.md)
- 🇵🇱 [Zależności stref i drugie źródło ciepła (po polsku)](documentation/pl/zone_dependencies.md)
- 🇵🇱 [Wejścia wody i kotła (po polsku)](documentation/pl/water_input.md)
- 🇵🇱 [Testy (po polsku)](documentation/pl/testing.md)
- 🇵🇱 [Diagnostyka (po polsku)](documentation/pl/diagnostics.md)

Dokumentacja jest też dostępna po angielsku ([README.md](README.md)) i po francusku ([README.fr.md](README.fr.md)).

## 👥 Autorzy

- [@KipK](https://github.com/KipK)
- [@gael1980](https://github.com/gael1980)

## 🤝 Współtworzenie

Wkład w projekt jest mile widziany! Wskazówki dotyczące rozwoju kodu i sposobu testowania znajdziesz w dokumentacji.

## 🙏 Podziękowania

- [@gael1980](https://github.com/gael1980) za naukowe podstawy i teorię, na których opiera się SmartPI
- [@caiusseverus](https://github.com/caiusseverus) za bardzo przydatny [symulator ogrzewania](https://github.com/caiusseverus/heating-simulator) oraz za pomoc w testowaniu SmartPI i dzielenie się wiedzą
- [@jmcollin](https://github.com/jmcollin78) za stworzenie [Versatile Thermostat](https://github.com/jmcollin78/versatile_thermostat)
- [@bontiv](https://github.com/bontiv) za [stronę internetową Versatile Thermostat](https://www.versatile-thermostat.org/)
- Wszystkim, którzy testowali SmartPI w czasie jego tworzenia

## ☕ Wsparcie

Jeśli SmartPI jest dla ciebie przydatny, możesz wesprzeć jego rozwój tutaj:

<p>
  <a href="https://www.buymeacoffee.com/kipk" target="_blank" rel="noopener noreferrer">
    <img src="https://cdn.buymeacoffee.com/buttons/v2/default-yellow.png" alt="Buy Me a Coffee" width="217" height="60" />
  </a>
</p>

## 📄 Licencja

Projekt jest udostępniony na warunkach licencji Apache 2. Szczegóły znajdziesz w pliku [LICENSE](LICENSE).
