$(document).ready(function() {

    var plone5 = $('[data-bundle="plone-legacy"]').length > 0;
    if (plone5) {
    }

    try {
        var order = DATATABLES_ORDER;
    } catch(e) {
        var order = [0, "asc"];
    }

    var tables = $('.datatable');
    if (tables.length > 0) {
        $('.datatable tfoot th.searchable').each(function () {
            if ($(this).children().length == 0) {
                var title = $('.datatable thead th').eq( $(this).index() ).text();
                $(this).html( '<input type="text" placeholder="Search '+title+'" />' );
            }
        });

        var table = $('.datatable').DataTable({

            columnDefs: [ {
              "targets": 'no-sort',
                         "orderable": false,
            } ],
            pageLength: 50,
            autoWidth: false,
            initComplete: function(settings, json) {
                $('.datatable').show();
            },
            order: order,
            aLengthMenu: [25, 50, 100, 250, 500, 750, 1000, 2000, 3000],
            // dom: 'TC<"clear">lfrtip',
            dom: 'C<"clear">lfrtip',
               tableTools: {
            "sSwfPath": "++resource++zchl.policy/DataTables/extensions/TableTools/swf/copy_csv_xls_pdf.swf"
            },
            language: {
                "sEmptyTable":      "Keine Daten in der Tabelle vorhanden",
                "sInfo":            "_START_ bis _END_ von _TOTAL_ Einträgen",
                "sInfoEmpty":       "0 bis 0 von 0 Einträgen",
                "sInfoFiltered":    "(gefiltert von _MAX_ Einträgen)",
                "sInfoPostFix":     "",
                "sInfoThousands":   ".",
                "sLengthMenu":      "_MENU_ Einträge anzeigen",
                "sLoadingRecords":  "Wird geladen...",
                "sProcessing":      "Bitte warten...",
                "sSearch":          "Suchen",
                "sZeroRecords":     "Keine Einträge vorhanden.",
                "oPaginate": {
                    "sFirst":       "Erste",
                    "sPrevious":    "Zurück",
                    "sNext":        "Nächste",
                    "sLast":        "Letzte"
                },
                "oAria": {
                    "sSortAscending":  ": aktivieren, um Spalte aufsteigend zu sortieren",
                    "sSortDescending": ": aktivieren, um Spalte absteigend zu sortieren"
                }
            }
        });

        table.columns().every( function () {
            var that = this;
            $( 'input', this.footer() ).on( 'keyup change', function () {
                that
                    .search( this.value )
                    .draw();
            } );
        } );
    }
});
