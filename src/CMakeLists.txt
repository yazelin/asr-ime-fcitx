# Build shared addon library (fcitx5 loads from <prefix>/lib/fcitx5)
add_library(asrimefcitxnative SHARED asrime.cpp)
target_link_libraries(asrimefcitxnative PRIVATE Fcitx5::Core Fcitx5::Utils)
install(TARGETS asrimefcitxnative DESTINATION "${FCITX_INSTALL_LIBDIR}/fcitx5")

# Addon config
configure_file(asrime-addon.conf.in.in asrime-addon.conf.in)
fcitx5_translate_desktop_file("${CMAKE_CURRENT_BINARY_DIR}/asrime-addon.conf.in" asrime-addon.conf)
install(
  FILES "${CMAKE_CURRENT_BINARY_DIR}/asrime-addon.conf"
  RENAME asrimefcitxnative.conf
  DESTINATION "${FCITX_INSTALL_PKGDATADIR}/addon"
)

# Input method registration
fcitx5_translate_desktop_file(asrime.conf.in asrime.conf)
install(FILES "${CMAKE_CURRENT_BINARY_DIR}/asrime.conf" DESTINATION "${FCITX_INSTALL_PKGDATADIR}/inputmethod")
